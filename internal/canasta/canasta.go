package canasta

import (
	"bufio"
	"crypto/rand"
	_ "embed"
	"encoding/hex"
	"fmt"
	"io/ioutil"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/git"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/sethvargo/go-password/password"
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
	// DefaultImageTag is the default tag to use for the Canasta image
	DefaultImageTag = "latest"
)

// GetDefaultImage returns the full default Canasta image reference
func GetDefaultImage() string {
	return fmt.Sprintf("%s/%s:%s", DefaultImageRegistry, DefaultImageName, DefaultImageTag)
}

// Embed README files for settings directories
//
//go:embed files/global-settings-README
var globalSettingsREADME string

//go:embed files/wiki-settings-README
var wikiSettingsREADME string

// GetImageWithTag returns the Canasta image reference with the specified tag
func GetImageWithTag(tag string) string {
	return fmt.Sprintf("%s/%s:%s", DefaultImageRegistry, DefaultImageName, tag)
}

// CloneStackRepo() accepts the orchestrator from the CLI,
// passes the corresponding repository link,
// and clones the repo to a new folder in the specified path.
// If localSourcePath is provided and contains a Canasta-DockerCompose directory, it copies from there instead.
func CloneStackRepo(orchestrator, canastaId string, path *string, localSourcePath string) error {
	*path += "/" + canastaId

	// Check if local Canasta-DockerCompose exists (only when building from source)
	if localSourcePath != "" {
		localDockerComposePath := filepath.Join(localSourcePath, "Canasta-DockerCompose")
		if info, err := os.Stat(localDockerComposePath); err == nil && info.IsDir() {
			logging.Print(fmt.Sprintf("Copying local Canasta-DockerCompose from %s to %s\n", localDockerComposePath, *path))
			// Create target directory
			if err := os.MkdirAll(*path, 0755); err != nil {
				return fmt.Errorf("failed to create directory: %w", err)
			}
			// Copy contents (trailing /. copies contents, not the directory itself)
			err, output := execute.Run("", "cp", "-r", localDockerComposePath+"/.", *path)
			if err != nil {
				return fmt.Errorf("failed to copy local Canasta-DockerCompose: %s", output)
			}
			return nil
		}
	}

	// Fall back to cloning from GitHub
	logging.Print(fmt.Sprintf("Cloning the %s stack repo to %s \n", orchestrator, *path))
	repo := orchestrators.GetRepoLink(orchestrator)
	err := git.Clone(repo, *path)
	return err
}

// CreateEnvFile creates the .env file for a new Canasta installation
// It starts with .env.example as the base, merges any custom env file if provided,
// and then applies the DB passwords and domain configuration
func CreateEnvFile(customEnvPath, installPath, workingDir, rootDBpass, wikiDBpass string) error {
	yamlPath := installPath + "/config/wikis.yaml"

	// Step 1: Copy .env.example as base
	examplePath := installPath + "/.env.example"
	logging.Print(fmt.Sprintf("Copying %s to %s/.env\n", examplePath, installPath))
	err, output := execute.Run("", "cp", examplePath, installPath+"/.env")
	if err != nil {
		return fmt.Errorf(output)
	}

	// Step 2: If custom env file provided, merge its values
	if customEnvPath != "" {
		if !strings.HasPrefix(customEnvPath, "/") {
			customEnvPath = workingDir + "/" + customEnvPath
		}
		logging.Print(fmt.Sprintf("Merging overrides from %s into %s/.env\n", customEnvPath, installPath))

		// Read custom env file
		customEnv := GetEnvVariable(customEnvPath)

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

	// Step 3: Apply domain configuration
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

	// Step 4: Apply DB passwords (command-line flags take precedence)
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
		return fmt.Errorf(output)
	}
	return nil
}

func CopySettings(installPath string) error {
	yamlPath := installPath + "/config/wikis.yaml"

	logging.Print(fmt.Sprintf("Copying settings from wikis.yaml at %s\n", yamlPath))
	WikiIDs, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return err
	}

	// Create config/settings/wikis directory if it doesn't exist
	wikisDir := filepath.Join(installPath, "config", "settings", "wikis")
	if err := os.MkdirAll(wikisDir, os.ModePerm); err != nil {
		return err
	}

	for i := len(WikiIDs) - 1; i >= 0; i-- {
		// Replace spaces to underlines and remove accented and non-alphanumeric characters
		id := strings.Replace(WikiIDs[i], " ", "_", -1)
		id = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(id,"")
		dirPath := filepath.Join(installPath, "config", "settings", "wikis", id)

		// Create the directory if it doesn't exist
		if err := os.MkdirAll(dirPath, os.ModePerm); err != nil {
			return err
		}

		// Write README with wiki ID
		readmePath := filepath.Join(dirPath, "README")
		content := strings.ReplaceAll(wikiSettingsREADME, "{WIKI_ID}", id)
		if err := os.WriteFile(readmePath, []byte(content), 0644); err != nil {
			return fmt.Errorf("failed to write README for %s: %w", id, err)
		}
	}

	// Create config/settings/global directory if it doesn't exist
	globalSettingsDir := filepath.Join(installPath, "config", "settings", "global")
	if err := os.MkdirAll(globalSettingsDir, os.ModePerm); err != nil {
		return err
	}

	// Write global README
	globalReadmePath := filepath.Join(globalSettingsDir, "README")
	if err := os.WriteFile(globalReadmePath, []byte(globalSettingsREADME), 0644); err != nil {
		return fmt.Errorf("failed to write global README: %w", err)
	}

	return nil
}

func CopySetting(installPath, id string) error {
	// Normalize wiki ID
	normalizedId := strings.Replace(id, " ", "_", -1)
	normalizedId = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(normalizedId, "")

	dirPath := filepath.Join(installPath, "config", "settings", "wikis", normalizedId)

	// Create the directory if it doesn't exist
	if err := os.MkdirAll(dirPath, os.ModePerm); err != nil {
		return err
	}

	// Write README with wiki ID
	readmePath := filepath.Join(dirPath, "README")
	content := strings.ReplaceAll(wikiSettingsREADME, "{WIKI_ID}", normalizedId)
	if err := os.WriteFile(readmePath, []byte(content), 0644); err != nil {
		return fmt.Errorf("failed to write README: %w", err)
	}

	return nil
}

// CopyWikiSettingFile copies a user-provided Settings.php file to the wiki's config directory
// Used when importing a wiki with a custom Settings.php
func CopyWikiSettingFile(installPath, wikiID, settingsFilePath, workingDir string) error {
	// Make path absolute if it's relative
	if !strings.HasPrefix(settingsFilePath, "/") {
		settingsFilePath = workingDir + "/" + settingsFilePath
	}

	// Normalize wikiID (replace spaces with underscores, remove non-alphanumeric)
	id := strings.Replace(wikiID, " ", "_", -1)
	id = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(id, "")
	dirPath := filepath.Join(installPath, "config", "settings", "wikis", id)

	// Create the directory if it doesn't exist
	if err := os.MkdirAll(dirPath, os.ModePerm); err != nil {
		return err
	}

	// Copy the provided file as Settings.php
	destPath := filepath.Join(dirPath, "Settings.php")
	logging.Print(fmt.Sprintf("Copying %s to %s\n", settingsFilePath, destPath))
	err, output := execute.Run("", "cp", settingsFilePath, destPath)
	if err != nil {
		return fmt.Errorf(output)
	}

	return nil
}

// CopyGlobalSettingFile copies a user-provided settings file to config/settings/global/ directory
// The original filename is preserved
func CopyGlobalSettingFile(installPath, settingsFilePath, workingDir string) error {
	// Make path absolute if it's relative
	if !strings.HasPrefix(settingsFilePath, "/") {
		settingsFilePath = workingDir + "/" + settingsFilePath
	}

	// Get the original filename
	filename := filepath.Base(settingsFilePath)
	dirPath := filepath.Join(installPath, "config", "settings", "global")

	// Create the directory if it doesn't exist
	if err := os.MkdirAll(dirPath, os.ModePerm); err != nil {
		return err
	}

	// Copy the provided file preserving its name
	destPath := filepath.Join(dirPath, filename)
	logging.Print(fmt.Sprintf("Copying %s to %s\n", settingsFilePath, destPath))
	err, output := execute.Run("", "cp", settingsFilePath, destPath)
	if err != nil {
		return fmt.Errorf(output)
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

// GenerateAndSaveSecretKey generates a secret key and saves it to .env as MW_SECRET_KEY
// This is used for all installations - both fresh installs and database imports
func GenerateAndSaveSecretKey(installPath string) error {
	secretKey, err := generateSecretKey()
	if err != nil {
		return fmt.Errorf("failed to generate secret key: %w", err)
	}

	logging.Print("Generating MW_SECRET_KEY and saving to .env\n")
	return SaveEnvVariable(installPath+"/.env", "MW_SECRET_KEY", secretKey)
}

func RewriteSettings(installPath string, WikiIDs []string) error {
	// Read wikis.yaml to get mapping of ID to site name
	yamlPath := installPath + "/config/wikis.yaml"
	wikisData, err := farmsettings.ReadWikisYamlWithNames(yamlPath)
	if err != nil {
		return err
	}

	for _, id := range WikiIDs {
		// Get the site name for this wiki ID
		siteName := id // Default to ID if not found
		for _, wiki := range wikisData {
			if wiki.ID == id {
				siteName = wiki.NAME
				if siteName == "" {
					siteName = id
				}
				break
			}
		}

		// Check new path first, fall back to legacy path
		filePath := filepath.Join(installPath, "config", "settings", "wikis", id, "LocalSettings.php")
		if _, err := os.Stat(filePath); os.IsNotExist(err) {
			filePath = filepath.Join(installPath, "config", id, "LocalSettings.php")
		}

		// Read the original file
		file, err := os.Open(filePath)
		if err != nil {
			return err
		}
		defer file.Close()

		// Read file line by line
		scanner := bufio.NewScanner(file)
		var lines []string
		for scanner.Scan() {
			line := scanner.Text()
			if strings.Contains(line, "#$wgSitename = ;") {
				line = "$wgSitename = \"" + siteName + "\";"
			}
			if strings.Contains(line, "#$wgMetaNamespace = ;") {
				line = "$wgMetaNamespace = \"" + id + "\";"
			}
			lines = append(lines, line)
		}

		// Create/Truncate the file for writing
		file, err = os.Create(filePath)
		if err != nil {
			return err
		}

		// Write back to file
		writer := bufio.NewWriter(file)
		for _, line := range lines {
			_, err = fmt.Fprintln(writer, line)
			if err != nil {
				return err
			}
		}
		if err = writer.Flush(); err != nil {
			return err
		}
	}
	return nil
}

// Function to remove duplicates from slice
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

	// Remove duplicates from ServerNames
	ServerNames = removeDuplicates(ServerNames)

	// Generate the new server names line
	var newLine strings.Builder
	for i, serverName := range ServerNames {
		if i > 0 {
			newLine.WriteString(", ")
		}
		// Strip any port from server name (e.g., "localhost:8443" -> "localhost")
		// since we append :{$HTTPS_PORT} which refers to the container's internal port
		if colonIdx := strings.LastIndex(serverName, ":"); colonIdx != -1 {
			serverName = serverName[:colonIdx]
		}
		newLine.WriteString(serverName)
		newLine.WriteString(":{$HTTPS_PORT}")
	}

	// Create/Truncate the file for writing
	file, err := os.Create(filePath)
	if err != nil {
		return err
	}

	// Write back to file
	writer := bufio.NewWriter(file)

	// Write server names line to file
	_, err = fmt.Fprintln(writer, newLine.String())
	if err != nil {
		return err
	}

	// Write empty line to file
	_, err = fmt.Fprintln(writer, "")
	if err != nil {
		return err
	}

	// Write reverse proxy line to file
	_, err = fmt.Fprintln(writer, "reverse_proxy varnish:80")
	if err != nil {
		return err
	}

	if err = writer.Flush(); err != nil {
		return err
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
	fmt.Printf("Saving password to %s\n", filePath)
	file, err := os.Create(filePath)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = file.WriteString(password)
	return err
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
	if err := ioutil.WriteFile(envPath, []byte(lines), 0644); err != nil {
		return err
	}
	return nil
}

// Get values saved inside the .env at the envPath
func GetEnvVariable(envPath string) map[string]string {
	EnvVariables := make(map[string]string)
	file_data, err := os.ReadFile(envPath)
	if err != nil {
		logging.Fatal(err)
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
	return EnvVariables
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

func DeleteConfigAndContainers(keepConfig bool, installationDir, orchestrator string) {
	fmt.Println("Removing containers")
	orchestrators.DeleteContainers(installationDir, orchestrator)
	fmt.Println("Deleting config files")
	orchestrators.DeleteConfig(installationDir)
	fmt.Println("Deleted all containers and config files")
}

func RemoveSettings(installPath, id string) error {
	// Check new path first (config/settings/wikis/<id>)
	newPath := filepath.Join(installPath, "config", "settings", "wikis", id)
	if _, err := os.Stat(newPath); err == nil {
		err, output := execute.Run("", "rm", "-rf", newPath)
		if err != nil {
			return fmt.Errorf(output)
		}
		return nil
	}

	// Check legacy path (config/<id>)
	legacyPath := filepath.Join(installPath, "config", id)
	if _, err := os.Stat(legacyPath); err == nil {
		err, output := execute.Run("", "rm", "-rf", legacyPath)
		if err != nil {
			return fmt.Errorf(output)
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
			return fmt.Errorf(output)
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
	if err := os.MkdirAll(wikisDir, os.ModePerm); err != nil {
		return err
	}

	// Copy the settings using the new path structure
	err = CopySetting(installPath, id)
	if err != nil {
		return err
	}

	return nil
}
