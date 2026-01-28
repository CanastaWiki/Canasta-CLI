package canasta

import (
	"bufio"
	"fmt"
	"io/ioutil"
	"os"
	"path/filepath"
	"strings"
	"regexp"

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
	if rootDBpass != "" {
		pass := "\"" + strings.ReplaceAll(rootDBpass, "\"", "\\\"") + "\""
		if err := SaveEnvVariable(installPath+"/.env", "MYSQL_PASSWORD", pass); err != nil {
			return err
		}
	}

	if wikiDBpass != "" {
		pass := "\"" + strings.ReplaceAll(wikiDBpass, "\"", "\\\"") + "\""
		if err := SaveEnvVariable(installPath+"/.env", "WIKI_DB_PASSWORD", pass); err != nil {
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

	logging.Print(fmt.Sprintf("Copying %s to %s/.env\n", yamlPath, installPath))
	WikiIDs, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return err
	}
	for i := len(WikiIDs) - 1; i >= 0; i-- {
		// Replace spaces to underlines and remove accented and non-alphanumeric characters
		id := strings.Replace(WikiIDs[i], " ", "_", -1)
		id = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(id,"")
		dirPath := installPath + fmt.Sprintf("/config/%s", id)

		// Create the directory if it doesn't exist
		if err := os.MkdirAll(dirPath, os.ModePerm); err != nil {
			return err
		}

		// Copy SettingsTemplate.php
		err, output := execute.Run("", "cp", installPath+"/config/SettingsTemplate.php", dirPath+"/Settings.php")
		if err != nil {
			return fmt.Errorf(output)
		}
	}
	return nil
}

func CopySetting(installPath, id string) error {
	dirPath := installPath + fmt.Sprintf("/config/%s", id)

	// Create the directory if it doesn't exist
	if err := os.MkdirAll(dirPath, os.ModePerm); err != nil {
		return err
	}

	// Copy SettingsTemplate.php to Settings.php
	err, output := execute.Run("", "cp", installPath+"/config/SettingsTemplate.php", dirPath+"/Settings.php")
	if err != nil {
		return fmt.Errorf(output)
	}

	return nil
}

// CopyWikiSettingFile copies a user-provided Settings.php file to the wiki's config directory
// Used when importing a wiki with a custom Settings.php instead of SettingsTemplate.php
func CopyWikiSettingFile(installPath, wikiID, settingsFilePath, workingDir string) error {
	// Make path absolute if it's relative
	if !strings.HasPrefix(settingsFilePath, "/") {
		settingsFilePath = workingDir + "/" + settingsFilePath
	}

	// Normalize wikiID (replace spaces with underscores, remove non-alphanumeric)
	id := strings.Replace(wikiID, " ", "_", -1)
	id = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(id, "")
	dirPath := installPath + fmt.Sprintf("/config/%s", id)

	// Create the directory if it doesn't exist
	if err := os.MkdirAll(dirPath, os.ModePerm); err != nil {
		return err
	}

	// Copy the provided file as Settings.php
	logging.Print(fmt.Sprintf("Copying %s to %s/Settings.php\n", settingsFilePath, dirPath))
	err, output := execute.Run("", "cp", settingsFilePath, dirPath+"/Settings.php")
	if err != nil {
		return fmt.Errorf(output)
	}

	return nil
}

// CopyGlobalSettingFile copies a user-provided settings file to config/settings/ directory
// The original filename is preserved
func CopyGlobalSettingFile(installPath, settingsFilePath, workingDir string) error {
	// Make path absolute if it's relative
	if !strings.HasPrefix(settingsFilePath, "/") {
		settingsFilePath = workingDir + "/" + settingsFilePath
	}

	// Get the original filename
	filename := filepath.Base(settingsFilePath)
	dirPath := filepath.Join(installPath, "config", "settings")

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

		filePath := installPath + "/config/" + id + "/LocalSettings.php"

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

func GeneratePasswords(workingDir string, canastaInfo CanastaVariables) (CanastaVariables, error) {
	var err error

	// Admin password: flag → auto-generate (saved to config/admin-password_{wikiid} later)
	if canastaInfo.AdminPassword == "" {
		canastaInfo.AdminPassword, err = password.Generate(30, 4, 6, false, true)
		if err != nil {
			return canastaInfo, err
		}
		// dollar signs in the password break the installer
		// https://phabricator.wikimedia.org/T355013
		canastaInfo.AdminPassword = strings.ReplaceAll(canastaInfo.AdminPassword, "$", "#")
		fmt.Printf("Generated admin password\n")
	}

	// Root DB password: flag → auto-generate (per-farm, saved to .env only)
	if canastaInfo.RootDBPassword == "" {
		canastaInfo.RootDBPassword, err = password.Generate(30, 4, 6, false, true)
		if err != nil {
			return canastaInfo, err
		}
		// dollar signs in the root DB password break the installer
		// https://phabricator.wikimedia.org/T355013
		canastaInfo.RootDBPassword = strings.ReplaceAll(canastaInfo.RootDBPassword, "$", "#")
		fmt.Printf("Generated root database password (will be saved to .env)\n")
	}

	// Wiki DB password: flag → auto-generate (per-farm, saved to .env only)
	if canastaInfo.WikiDBUsername == "root" {
		canastaInfo.WikiDBPassword = canastaInfo.RootDBPassword
	} else {
		if canastaInfo.WikiDBPassword == "" {
			canastaInfo.WikiDBPassword, err = password.Generate(30, 4, 6, false, true)
			if err != nil {
				return canastaInfo, err
			}
			// dollar signs in the wiki DB password break the installer
			canastaInfo.WikiDBPassword = strings.ReplaceAll(canastaInfo.WikiDBPassword, "$", "#")
			fmt.Printf("Generated wiki database password (will be saved to .env)\n")
		}
	}

	return canastaInfo, nil
}

// GeneratePassword generates a random password for the specified purpose
func GeneratePassword(purpose string) (string, error) {
	generatedPassword, err := password.Generate(30, 4, 6, false, true)
	if err != nil {
		return "", err
	}
	// dollar signs in passwords break the installer
	// https://phabricator.wikimedia.org/T355013
	generatedPassword = strings.ReplaceAll(generatedPassword, "$", "#")
	return generatedPassword, nil
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
		list := strings.Split(variable, "=")
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
	// Prepare the file path
	filePath := filepath.Join(installPath, "config", id)

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

	//Copy the Localsettings
	err = CopySetting(installPath, id)
	if err != nil {
		return err
	}

	return nil
}
