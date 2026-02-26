package canasta

import (
	"bufio"
	"crypto/rand"
	"embed"
	"encoding/hex"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
	"github.com/sethvargo/go-password/password"
	"golang.org/x/crypto/bcrypt"
)

type CanastaVariables struct {
	Id             string
	AdminPassword  string
	AdminName      string
	RootDBPassword string
	WikiDBUsername string
	WikiDBPassword string
}

const (
	// DefaultImageRegistry is the container registry for Canasta images
	DefaultImageRegistry = "ghcr.io/canastawiki"
	// DefaultImageName is the name of the Canasta image
	DefaultImageName = "canasta"
	// DefaultImageTag is the default tag to use for the Canasta image.
	// This should match the current stable Canasta release. Update it
	// when cutting a new CLI release to pair with a new Canasta version.
	DefaultImageTag = "3.2.1"
)

// GetDefaultImage returns the full default Canasta image reference
func GetDefaultImage() string {
	return fmt.Sprintf("%s/%s:%s", DefaultImageRegistry, DefaultImageName, DefaultImageTag)
}


// installationTemplate contains the shared installation directory structure.
// These files are common to all orchestrators (Docker Compose, Kubernetes, etc.)
// and are copied to the installation directory during create.
//
//go:embed all:installation-template
var installationTemplate embed.FS

// userEditablePaths lists template files that users may customize.
// These are only written during create (no-clobber) and never overwritten during upgrade.
var userEditablePaths = map[string]bool{
	".env":                                       true,
	"my.cnf":                                     true,
	"config/default.vcl":                         true,
	"config/Caddyfile.site":                      true,
	"config/Caddyfile.global":                    true,
	"config/settings/global/Vector.php":          true,
	"config/settings/global/CanastaFooterIcon.php": true,
}

// CopyInstallationTemplate copies the embedded installation template files to the
// destination directory. These are shared files common to all orchestrators.
// Files are only written if they don't already exist (no-clobber), so orchestrator
// repos can provide their own versions of files if needed.
func CopyInstallationTemplate(destPath string) error {
	logging.Print("Copying installation template files\n")
	return copyTemplate(destPath, false)
}

// UpdateInstallationTemplate re-applies the embedded template during upgrade.
// User-editable files are only created if missing (no-clobber).
// CLI-managed files (READMEs, etc.) are always updated to match the current CLI version.
func UpdateInstallationTemplate(destPath string) error {
	logging.Print("Updating installation template files\n")
	return copyTemplate(destPath, true)
}

// copyTemplate walks the embedded installation template and copies files to destPath.
// If upgrading is true, CLI-managed files are force-updated while user-editable files
// use no-clobber. If upgrading is false, all files use no-clobber.
func copyTemplate(destPath string, upgrading bool) error {
	return fs.WalkDir(installationTemplate, "installation-template", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}

		// Strip the "installation-template" prefix to get the relative path
		relPath, err := filepath.Rel("installation-template", path)
		if err != nil {
			return err
		}

		// Skip the root directory itself
		if relPath == "." {
			return nil
		}

		targetPath := filepath.Join(destPath, relPath)

		if d.IsDir() {
			return os.MkdirAll(targetPath, 0755)
		}

		// Skip .gitkeep files — they're only used to preserve directory
		// structure in the embedded FS and are not needed in the installation
		if d.Name() == ".gitkeep" {
			return nil
		}

		// No-clobber for user-editable files (always) and all files (during create)
		if !upgrading || userEditablePaths[relPath] {
			if _, err := os.Stat(targetPath); err == nil {
				return nil
			}
		}

		data, err := installationTemplate.ReadFile(path)
		if err != nil {
			return fmt.Errorf("failed to read embedded file %s: %w", path, err)
		}

		return os.WriteFile(targetPath, data, 0644)
	})
}

// UpdateEnvFile configures the .env file for a new Canasta installation.
// The base .env is provided by the installation template (CopyInstallationTemplate).
// This function merges any custom env file if provided,
// and then applies the DB passwords and domain configuration.
func UpdateEnvFile(customEnvPath, installPath, workingDir, rootDBpass, wikiDBpass string) error {
	yamlPath := installPath + "/config/wikis.yaml"

	// If custom env file provided, merge its values
	if customEnvPath != "" {
		if !filepath.IsAbs(customEnvPath) {
			customEnvPath = filepath.Join(workingDir, customEnvPath)
		}
		logging.Print(fmt.Sprintf("Merging overrides from %s into %s/.env\n", customEnvPath, installPath))

		// Read custom env file
		customEnv, err := GetEnvVariable(customEnvPath)
		if err != nil {
			return err
		}

		// Check for problematic characters in password fields
		if pw, ok := customEnv["MYSQL_PASSWORD"]; ok {
			warnIfProblematicPassword("MYSQL_PASSWORD", pw)
		}
		if pw, ok := customEnv["WIKI_DB_PASSWORD"]; ok {
			warnIfProblematicPassword("WIKI_DB_PASSWORD", pw)
		}

		// Apply each override to .env
		for key, value := range customEnv {
			if err := SaveEnvVariable(installPath+"/.env", key, value); err != nil {
				return err
			}
		}
	}

	// Apply domain configuration
	_, domainNames, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return err
	}
	if err := SaveEnvVariable(installPath+"/.env", "MW_SITE_SERVER", "https://"+domainNames[0]); err != nil {
		return err
	}
	if err := SaveEnvVariable(installPath+"/.env", "MW_SITE_FQDN", domainNames[0]); err != nil {
		return err
	}

	// Apply DB passwords (command-line flags take precedence)
	// Note: Don't wrap in quotes - docker-compose includes quotes as part of the value,
	// causing a mismatch with the CLI which strips quotes when reading.
	if rootDBpass != "" {
		if err := SaveEnvVariable(installPath+"/.env", "MYSQL_PASSWORD", rootDBpass); err != nil {
			return err
		}
	}

	if wikiDBpass != "" {
		if err := SaveEnvVariable(installPath+"/.env", "WIKI_DB_PASSWORD", wikiDBpass); err != nil {
			return err
		}
	}

	return nil
}


func CopyYaml(yamlPath, installPath string) error {
	logging.Print(fmt.Sprintf("Copying %s to %s/config/wikis.yaml\n", yamlPath, installPath))
	err, output := execute.Run("", "cp", yamlPath, installPath+"/config/wikis.yaml")
	if err != nil {
		return fmt.Errorf("%s", output)
	}
	return nil
}

// NormalizeWikiID converts a wiki ID to a filesystem-safe form by replacing
// spaces with underscores and stripping non-alphanumeric characters.
func NormalizeWikiID(id string) string {
	normalized := strings.ReplaceAll(id, " ", "_")
	return regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(normalized, "")
}

func CopySettings(installPath string) error {
	yamlPath := installPath + "/config/wikis.yaml"

	logging.Print(fmt.Sprintf("Copying settings from wikis.yaml at %s\n", yamlPath))
	WikiIDs, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return err
	}

	// Read the wiki README template from the installation template
	wikiREADME, err := installationTemplate.ReadFile("installation-template/config/settings/wikis/README")
	if err != nil {
		return fmt.Errorf("failed to read embedded wiki README: %w", err)
	}

	for i := len(WikiIDs) - 1; i >= 0; i-- {
		id := NormalizeWikiID(WikiIDs[i])
		dirPath := filepath.Join(installPath, "config", "settings", "wikis", id)

		// Create the directory if it doesn't exist
		if err := os.MkdirAll(dirPath, 0755); err != nil {
			return err
		}

		// Copy README into the wiki's settings directory
		readmePath := filepath.Join(dirPath, "README")
		if err := os.WriteFile(readmePath, wikiREADME, 0644); err != nil {
			return fmt.Errorf("failed to write README for %s: %w", id, err)
		}
	}

	return nil
}

func CopySetting(installPath, id string) error {
	normalizedId := NormalizeWikiID(id)

	dirPath := filepath.Join(installPath, "config", "settings", "wikis", normalizedId)

	// Create the directory if it doesn't exist
	if err := os.MkdirAll(dirPath, 0755); err != nil {
		return err
	}

	// Copy README into the wiki's settings directory
	wikiREADME, err := installationTemplate.ReadFile("installation-template/config/settings/wikis/README")
	if err != nil {
		return fmt.Errorf("failed to read embedded wiki README: %w", err)
	}
	readmePath := filepath.Join(dirPath, "README")
	if err := os.WriteFile(readmePath, wikiREADME, 0644); err != nil {
		return fmt.Errorf("failed to write README: %w", err)
	}

	return nil
}

// CopyWikiSettingFile copies a user-provided Settings.php file to the wiki's config directory
// Used when importing a wiki with a custom Settings.php
func CopyWikiSettingFile(installPath, wikiID, settingsFilePath, workingDir string) error {
	// Make path absolute if it's relative
	if !filepath.IsAbs(settingsFilePath) {
		settingsFilePath = filepath.Join(workingDir, settingsFilePath)
	}

	id := NormalizeWikiID(wikiID)
	dirPath := filepath.Join(installPath, "config", "settings", "wikis", id)

	// Create the directory if it doesn't exist
	if err := os.MkdirAll(dirPath, 0755); err != nil {
		return err
	}

	// Copy the provided file as Settings.php
	destPath := filepath.Join(dirPath, "Settings.php")
	logging.Print(fmt.Sprintf("Copying %s to %s\n", settingsFilePath, destPath))
	err, output := execute.Run("", "cp", settingsFilePath, destPath)
	if err != nil {
		return fmt.Errorf("%s", output)
	}

	return nil
}

// CopyGlobalSettingFile copies a user-provided settings file to config/settings/global/ directory
// The original filename is preserved
func CopyGlobalSettingFile(installPath, settingsFilePath, workingDir string) error {
	// Make path absolute if it's relative
	if !filepath.IsAbs(settingsFilePath) {
		settingsFilePath = filepath.Join(workingDir, settingsFilePath)
	}

	// Get the original filename
	filename := filepath.Base(settingsFilePath)
	dirPath := filepath.Join(installPath, "config", "settings", "global")

	// Create the directory if it doesn't exist
	if err := os.MkdirAll(dirPath, 0755); err != nil {
		return err
	}

	// Copy the provided file preserving its name
	destPath := filepath.Join(dirPath, filename)
	logging.Print(fmt.Sprintf("Copying %s to %s\n", settingsFilePath, destPath))
	err, output := execute.Run("", "cp", settingsFilePath, destPath)
	if err != nil {
		return fmt.Errorf("%s", output)
	}

	return nil
}

// ValidateDatabasePath validates that the database file path has a valid extension (.sql or .sql.gz)
func ValidateDatabasePath(path string) error {
	if !strings.HasSuffix(path, ".sql") && !strings.HasSuffix(path, ".sql.gz") {
		return fmt.Errorf("database dump must have .sql or .sql.gz extension")
	}
	return nil
}

// generateSecretKey generates a random 64-character hex string for $wgSecretKey
func generateSecretKey() (string, error) {
	bytes := make([]byte, 32)
	if _, err := rand.Read(bytes); err != nil {
		return "", err
	}
	return hex.EncodeToString(bytes), nil
}

// GenerateAndSaveSecretKey generates a secret key and saves it to .env as MW_SECRET_KEY,
// unless MW_SECRET_KEY is already set (e.g. provided by the user via -e flag).
func GenerateAndSaveSecretKey(installPath string) error {
	envPath := installPath + "/.env"
	envVars, err := GetEnvVariable(envPath)
	if err != nil {
		return err
	}
	if val, ok := envVars["MW_SECRET_KEY"]; ok && val != "" {
		logging.Print("MW_SECRET_KEY already set in .env, skipping generation\n")
		return nil
	}

	secretKey, err := generateSecretKey()
	if err != nil {
		return fmt.Errorf("failed to generate secret key: %w", err)
	}

	logging.Print("Generating MW_SECRET_KEY and saving to .env\n")
	return SaveEnvVariable(envPath, "MW_SECRET_KEY", secretKey)
}

// ContainsProfile checks if a comma-separated profile string contains the given profile name.
func ContainsProfile(profiles, target string) bool {
	for _, p := range strings.Split(profiles, ",") {
		if strings.TrimSpace(p) == target {
			return true
		}
	}
	return false
}

// IsObservabilityEnabled returns true when CANASTA_ENABLE_OBSERVABILITY is set
// to "true" (case-insensitive) in the given env vars map.
func IsObservabilityEnabled(envVars map[string]string) bool {
	return strings.EqualFold(envVars["CANASTA_ENABLE_OBSERVABILITY"], "true")
}

// IsElasticsearchEnabled returns true when CANASTA_ENABLE_ELASTICSEARCH is set
// to "true" (case-insensitive) in the given env vars map.
func IsElasticsearchEnabled(envVars map[string]string) bool {
	return strings.EqualFold(envVars["CANASTA_ENABLE_ELASTICSEARCH"], "true")
}

// EnsureObservabilityCredentials checks if CANASTA_ENABLE_OBSERVABILITY=true in .env.
// If active, it ensures OS_USER, OS_PASSWORD, and OS_PASSWORD_HASH are set.
// Returns true if observability is enabled.
func EnsureObservabilityCredentials(installPath string) (bool, error) {
	envPath := installPath + "/.env"
	envVars, err := GetEnvVariable(envPath)
	if err != nil {
		return false, err
	}

	if !IsObservabilityEnabled(envVars) {
		return false, nil
	}

	// Set OS_USER to "admin" if not present
	if envVars["OS_USER"] == "" {
		if err := SaveEnvVariable(envPath, "OS_USER", "admin"); err != nil {
			return true, fmt.Errorf("failed to save OS_USER: %w", err)
		}
		logging.Print("Setting OS_USER=admin in .env\n")
	}

	// Generate OS_PASSWORD if not present
	if envVars["OS_PASSWORD"] == "" {
		pw, err := GeneratePassword("OpenSearch")
		if err != nil {
			return true, fmt.Errorf("failed to generate OS_PASSWORD: %w", err)
		}
		if err := SaveEnvVariable(envPath, "OS_PASSWORD", pw); err != nil {
			return true, fmt.Errorf("failed to save OS_PASSWORD: %w", err)
		}
		logging.Print("Generating OS_PASSWORD and saving to .env\n")
		// Re-read so we can hash it below
		envVars["OS_PASSWORD"] = pw
	}

	// Compute bcrypt hash of OS_PASSWORD and save as OS_PASSWORD_HASH if not present
	if envVars["OS_PASSWORD_HASH"] == "" {
		hash, err := bcrypt.GenerateFromPassword([]byte(envVars["OS_PASSWORD"]), bcrypt.DefaultCost)
		if err != nil {
			return true, fmt.Errorf("failed to hash OS_PASSWORD: %w", err)
		}
		if err := SaveEnvVariable(envPath, "OS_PASSWORD_HASH", string(hash)); err != nil {
			return true, fmt.Errorf("failed to save OS_PASSWORD_HASH: %w", err)
		}
		logging.Print("Generating OS_PASSWORD_HASH and saving to .env\n")
	}

	return true, nil
}

// removeDuplicates removes duplicate strings from a slice.
func removeDuplicates(slice []string) []string {
	keys := make(map[string]bool)
	list := []string{}

	for _, entry := range slice {
		if _, value := keys[entry]; !value {
			keys[entry] = true
			list = append(list, entry)
		}
	}
	return list
}

func RewriteCaddy(installPath string) error {
	_, ServerNames, _, err := farmsettings.ReadWikisYaml(installPath + "/config/wikis.yaml")
	if err != nil {
		return err
	}
	filePath := installPath + "/config/Caddyfile"

	// Check if CADDY_AUTO_HTTPS is set to "off" in .env (SSL terminated upstream)
	envPath := installPath + "/.env"
	envVars, err := GetEnvVariable(envPath)
	if err != nil {
		return err
	}
	httpOnly := strings.ToLower(envVars["CADDY_AUTO_HTTPS"]) == "off"

	// Check if observability is enabled
	observable := IsObservabilityEnabled(envVars)
	if observable {
		if envVars["OS_USER"] == "" || envVars["OS_PASSWORD_HASH"] == "" {
			return fmt.Errorf("observability is enabled but OS_USER or OS_PASSWORD_HASH is missing from .env; run 'canasta upgrade' to generate credentials")
		}
	}

	// Remove duplicates from ServerNames
	ServerNames = removeDuplicates(ServerNames)

	if len(ServerNames) == 0 {
		return fmt.Errorf("no server names found in wikis.yaml")
	}

	// Generate the site address from FQDNs
	var siteAddress strings.Builder
	for i, serverName := range ServerNames {
		if i > 0 {
			siteAddress.WriteString(", ")
		}
		// Strip any port from server name (e.g., "localhost:8443" -> "localhost")
		if colonIdx := strings.LastIndex(serverName, ":"); colonIdx != -1 {
			serverName = serverName[:colonIdx]
		}
		// Use explicit http:// when SSL is terminated upstream
		if httpOnly {
			siteAddress.WriteString("http://")
		}
		siteAddress.WriteString(serverName)
	}

	// Create/Truncate the file for writing
	file, err := os.Create(filePath)
	if err != nil {
		return err
	}
	defer file.Close()

	// Write back to file
	writer := bufio.NewWriter(file)

	// Helper to write lines and track errors
	var writeErr error
	writeLine := func(line string) {
		if writeErr == nil {
			_, writeErr = fmt.Fprintln(writer, line)
		}
	}

	// Write header comment
	writeLine("# Auto-generated by Canasta CLI — DO NOT EDIT")
	writeLine("# To add custom Caddy directives, edit Caddyfile.site instead.")
	writeLine("# For global Caddy options or additional site blocks, edit Caddyfile.global instead.")
	writeLine("")

	// Import global config at top level (allows global options, extra site blocks, or both)
	writeLine("import /etc/caddy/Caddyfile.global")
	writeLine("")

	// Write site block
	writeLine(siteAddress.String() + " {")
	writeLine("    import /etc/caddy/Caddyfile.site")
	writeLine("")
	if observable {
		writeLine("    @opensearch path /opensearch /opensearch/*")
		writeLine("    handle @opensearch {")
		writeLine("        basicauth {")
		writeLine("            " + envVars["OS_USER"] + " " + envVars["OS_PASSWORD_HASH"])
		writeLine("        }")
		writeLine("        reverse_proxy opensearch-dashboards:5601")
		writeLine("    }")
		writeLine("")
		writeLine("    handle {")
		writeLine("        reverse_proxy varnish:80")
		writeLine("    }")
	} else {
		writeLine("    reverse_proxy varnish:80")
	}
	writeLine("")
	writeLine("    log {")
	writeLine("        output file /var/log/caddy/access.log")
	writeLine("    }")
	writeLine("}")

	if writeErr != nil {
		return writeErr
	}

	if err = writer.Flush(); err != nil {
		return err
	}

	return nil
}

// CreateCaddyfileSite creates config/Caddyfile.site from the installation template.
// It only writes the file if it doesn't already exist (no-clobber).
func CreateCaddyfileSite(installPath string) error {
	filePath := filepath.Join(installPath, "config", "Caddyfile.site")

	// Don't overwrite if file already exists
	if _, err := os.Stat(filePath); err == nil {
		return nil
	}

	data, err := installationTemplate.ReadFile("installation-template/config/Caddyfile.site")
	if err != nil {
		return fmt.Errorf("failed to read embedded Caddyfile.site: %w", err)
	}
	return os.WriteFile(filePath, data, 0644)
}

// CreateCaddyfileGlobal creates config/Caddyfile.global from the installation template.
// It only writes the file if it doesn't already exist (no-clobber).
func CreateCaddyfileGlobal(installPath string) error {
	filePath := filepath.Join(installPath, "config", "Caddyfile.global")

	// Don't overwrite if file already exists
	if _, err := os.Stat(filePath); err == nil {
		return nil
	}

	data, err := installationTemplate.ReadFile("installation-template/config/Caddyfile.global")
	if err != nil {
		return fmt.Errorf("failed to read embedded Caddyfile.global: %w", err)
	}
	return os.WriteFile(filePath, data, 0644)
}

// CopyComposerFile copies a user-provided composer.local.json to config/composer.local.json.
func CopyComposerFile(installPath, sourceFilename, workingDir string) error {
	if !filepath.IsAbs(sourceFilename) {
		sourceFilename = filepath.Join(workingDir, sourceFilename)
	}
	destPath := filepath.Join(installPath, "config", "composer.local.json")
	logging.Print(fmt.Sprintf("Copying %s to %s\n", sourceFilename, destPath))
	err, output := execute.Run("", "cp", sourceFilename, destPath)
	if err != nil {
		return fmt.Errorf("%s", output)
	}
	return nil
}

// safePasswordGenerator creates a password generator with symbols that are safe
// for use in .env files and shell commands. Avoids: = # $ " ' ` \ ! & | ; < > etc.
func safePasswordGenerator() (*password.Generator, error) {
	return password.NewGenerator(&password.GeneratorInput{
		Symbols: "@%^-_+.,:",
	})
}

// problematicPasswordChars are characters that may cause issues in .env files or shell commands
const problematicPasswordChars = "=#$\"'`\\!&|;<>"

// warnIfProblematicPassword checks if a password contains characters that may cause issues
// and prints a warning if so.
func warnIfProblematicPassword(varName, value string) {
	var found []string
	for _, char := range problematicPasswordChars {
		if strings.ContainsRune(value, char) {
			found = append(found, string(char))
		}
	}
	if len(found) > 0 {
		fmt.Printf("Warning: %s contains characters that may cause issues: %s\n", varName, strings.Join(found, " "))
		fmt.Printf("  Consider using only alphanumeric characters and safe symbols: @%%^-_+.,:\n")
	}
}

// GenerateDBPasswords generates database passwords (root and wiki DB).
// This should always be called, even when importing a database.
func GenerateDBPasswords(canastaInfo CanastaVariables) (CanastaVariables, error) {
	var err error

	gen, err := safePasswordGenerator()
	if err != nil {
		return canastaInfo, err
	}

	// Root DB password: flag → auto-generate (per-farm, saved to .env only)
	if canastaInfo.RootDBPassword == "" {
		canastaInfo.RootDBPassword, err = gen.Generate(30, 4, 6, false, true)
		if err != nil {
			return canastaInfo, err
		}
		fmt.Printf("Generated root database password (will be saved to .env)\n")
	}

	// Wiki DB password: flag → auto-generate (per-farm, saved to .env only)
	if canastaInfo.WikiDBUsername == "root" {
		canastaInfo.WikiDBPassword = canastaInfo.RootDBPassword
	} else {
		if canastaInfo.WikiDBPassword == "" {
			canastaInfo.WikiDBPassword, err = gen.Generate(30, 4, 6, false, true)
			if err != nil {
				return canastaInfo, err
			}
			fmt.Printf("Generated wiki database password (will be saved to .env)\n")
		}
	}

	return canastaInfo, nil
}

// GenerateAdminPassword generates an admin password if not provided.
// This should only be called when NOT importing a database (i.e., running install.php).
func GenerateAdminPassword(canastaInfo CanastaVariables) (CanastaVariables, error) {
	var err error

	gen, err := safePasswordGenerator()
	if err != nil {
		return canastaInfo, err
	}

	// Admin password: flag → auto-generate (saved to config/admin-password_{wikiid} later)
	if canastaInfo.AdminPassword == "" {
		canastaInfo.AdminPassword, err = gen.Generate(30, 4, 6, false, true)
		if err != nil {
			return canastaInfo, err
		}
		fmt.Printf("Generated admin password\n")
	}

	return canastaInfo, nil
}

// GeneratePasswords generates all passwords (admin and database).
// For backward compatibility - calls both GenerateAdminPassword and GenerateDBPasswords.
func GeneratePasswords(workingDir string, canastaInfo CanastaVariables) (CanastaVariables, error) {
	var err error

	canastaInfo, err = GenerateAdminPassword(canastaInfo)
	if err != nil {
		return canastaInfo, err
	}

	canastaInfo, err = GenerateDBPasswords(canastaInfo)
	if err != nil {
		return canastaInfo, err
	}

	return canastaInfo, nil
}

// GeneratePassword generates a random password for the specified purpose
func GeneratePassword(purpose string) (string, error) {
	gen, err := safePasswordGenerator()
	if err != nil {
		return "", err
	}
	return gen.Generate(30, 4, 6, false, true)
}

// SavePasswordToFile saves a password to a file in the specified directory
func SavePasswordToFile(directory, filename, password string) error {
	filePath := filepath.Join(directory, filename)
	spinner.Print(fmt.Sprintf("Saving password to %s\n", filePath))
	file, err := os.Create(filePath)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = file.WriteString(password)
	return err
}


// DeleteEnvVariable removes a key from the .env file.
// Returns an error if the key is not found.
func DeleteEnvVariable(envPath, key string) error {
	file, err := os.ReadFile(envPath)
	if err != nil {
		return err
	}
	data := string(file)
	list := strings.Split(data, "\n")
	found := false
	result := make([]string, 0, len(list))
	for _, line := range list {
		if strings.HasPrefix(line, key+"=") {
			found = true
			continue
		}
		result = append(result, line)
	}
	if !found {
		return fmt.Errorf("key %q not found in %s", key, envPath)
	}
	lines := strings.Join(result, "\n")
	return os.WriteFile(envPath, []byte(lines), 0644)
}

// Make changes to the .env file at the installation directory
// If the key exists, it updates the value; if not, it appends the key=value pair
func SaveEnvVariable(envPath, key, value string) error {
	file, err := os.ReadFile(envPath)
	if err != nil {
		return err
	}
	data := string(file)
	list := strings.Split(data, "\n")
	found := false
	for index, line := range list {
		if strings.HasPrefix(line, key+"=") {
			list[index] = fmt.Sprintf("%s=%s", key, value)
			found = true
			break
		}
	}
	if !found {
		// Append new key=value pair
		list = append(list, fmt.Sprintf("%s=%s", key, value))
	}
	lines := strings.Join(list, "\n")
	if err := os.WriteFile(envPath, []byte(lines), 0644); err != nil {
		return err
	}
	return nil
}

// Get values saved inside the .env at the envPath
func GetEnvVariable(envPath string) (map[string]string, error) {
	EnvVariables := make(map[string]string)
	file_data, err := os.ReadFile(envPath)
	if err != nil {
		return nil, err
	}
	data := strings.TrimSuffix(string(file_data), "\n")
	variable_list := strings.Split(data, "\n")
	for _, variable := range variable_list {
		// Use SplitN to only split on first "=" - values may contain "=" characters
		list := strings.SplitN(variable, "=", 2)
		if len(list) < 2 {
			continue
		}
		// Strip surrounding quotes from the value
		value := list[1]
		if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
			value = value[1 : len(value)-1]
		}
		EnvVariables[list[0]] = value
	}
	return EnvVariables, nil
}

// Checking Installation existence
func CheckCanastaId(instance config.Installation) (config.Installation, error) {
	var err error
	if instance.Id != "" {
		if instance, err = config.GetDetails(instance.Id); err != nil {
			return instance, err
		}
	} else {
		if instance.Id, err = config.GetCanastaID(instance.Path); err != nil {
			return instance, err
		}
		if instance, err = config.GetDetails(instance.Id); err != nil {
			return instance, err
		}
	}
	return instance, nil
}

func RemoveSettings(installPath, id string) error {
	// Check new path first (config/settings/wikis/<id>)
	newPath := filepath.Join(installPath, "config", "settings", "wikis", id)
	if _, err := os.Stat(newPath); err == nil {
		err, output := execute.Run("", "rm", "-rf", newPath)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
		return nil
	}

	// Check legacy path (config/<id>)
	legacyPath := filepath.Join(installPath, "config", id)
	if _, err := os.Stat(legacyPath); err == nil {
		err, output := execute.Run("", "rm", "-rf", legacyPath)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	return nil
}

func RemoveImages(installPath, id string) error {
	// Prepare the file path
	filePath := filepath.Join(installPath, "images", id)

	// Check if the file exists
	if _, err := os.Stat(filePath); err == nil {
		// If the file exists, remove it
		err, output := execute.Run("", "rm", "-rf", filePath)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	} else if os.IsNotExist(err) {
		// File does not exist, do nothing
		return nil
	} else {
		// File may or may not exist. See the specific error
		return err
	}
	return nil
}

func RemovePublicAssets(installPath, id string) error {
	// Prepare the file path
	filePath := filepath.Join(installPath, "public_assets", id)

	// Check if the file exists
	if _, err := os.Stat(filePath); err == nil {
		// If the file exists, remove it
		err, output := execute.Run("", "rm", "-rf", filePath)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	} else if os.IsNotExist(err) {
		// File does not exist, do nothing
		return nil
	} else {
		// File may or may not exist. See the specific error
		return err
	}
	return nil
}

func MigrateToNewVersion(installPath string) error {
	// Determine the path to the wikis.yaml file
	yamlPath := filepath.Join(installPath, "config", "wikis.yaml")

	// Check if the file already exists
	if _, err := os.Stat(yamlPath); err == nil {
		// File exists, assume the user is already using the new configuration
		return nil
	} else if !os.IsNotExist(err) {
		// If the error is not because the file does not exist, return it
		return err
	}

	// Open the .env file
	envFile, err := os.Open(filepath.Join(installPath, ".env"))
	if err != nil {
		return err
	}
	defer envFile.Close()

	// Read the environment variables from the .env file
	envMap := make(map[string]string)
	scanner := bufio.NewScanner(envFile)
	// Default wiki ID for migration from old single-wiki installations
	id := "my_wiki"

	for scanner.Scan() {
		line := scanner.Text()
		splitLine := strings.SplitN(line, "=", 2)
		if len(splitLine) != 2 {
			continue
		}
		envMap[splitLine[0]] = splitLine[1]
	}

	if err := scanner.Err(); err != nil {
		return err
	}

	// Remove the "http://" or "https://" prefix from MW_SITE_SERVER variable
	mwSiteServer := strings.TrimPrefix(envMap["MW_SITE_SERVER"], "http://")
	mwSiteServer = strings.TrimPrefix(mwSiteServer, "https://")

	// Create the wikis.yaml file using farmsettings.GenerateWikisYaml
	// Pass empty string for siteName to default to id
	_, err = farmsettings.GenerateWikisYaml(yamlPath, id, mwSiteServer, "")
	if err != nil {
		return err
	}

	// Create config/settings/wikis directory
	wikisDir := filepath.Join(installPath, "config", "settings", "wikis")
	if err := os.MkdirAll(wikisDir, 0755); err != nil {
		return err
	}

	// Copy the settings using the new path structure
	err = CopySetting(installPath, id)
	if err != nil {
		return err
	}

	return nil
}
