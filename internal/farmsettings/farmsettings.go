package farmsettings

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	yaml "gopkg.in/yaml.v2"

	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
)

type Wiki struct {
	ID   string `yaml:"id"`
	URL  string `yaml:"url"`
	NAME string `yaml:"name"`
}

type Wikis struct {
	Wikis []Wiki `yaml:"wikis"`
}

// reservedNames are wiki IDs and URL paths that conflict with internal routes.
var reservedNames = []string{"settings", "images", "w", "wiki", "wikis"}

// ValidateWikiID validates that wikiID doesn't contain invalid characters or reserved names
func ValidateWikiID(wikiID string) error {
	// Check if wikiID contains a hyphen (-)
	if strings.Contains(wikiID, "-") {
		return fmt.Errorf("The character '-' is not allowed in wikiID")
	}

	// Check if wikiID is one of the reserved names
	for _, name := range reservedNames {
		if wikiID == name {
			return fmt.Errorf("%s cannot be used as wikiID", wikiID)
		}
	}

	// If it passes the checks, return nil (no error)
	return nil
}

// ValidateWikiPath validates that the URL path component doesn't use a reserved name
func ValidateWikiPath(wikiPath string) error {
	if wikiPath == "" {
		return nil
	}

	for _, name := range reservedNames {
		if wikiPath == name {
			return fmt.Errorf("%q cannot be used as a wiki URL path", wikiPath)
		}
	}

	return nil
}

func GenerateWikisYaml(filePath, wikiID, domain, siteName string) (string, error) {
	if siteName == "" {
		siteName = wikiID
	}
	wikis := Wikis{}
	wikis.Wikis = append(wikis.Wikis, Wiki{ID: wikiID, URL: domain, NAME: siteName})

	out, err := yaml.Marshal(&wikis)
	if err != nil {
		return "", err
	}

	err = os.WriteFile(filePath, out, permissions.FilePermission)
	if err != nil {
		return "", err
	}

	spinner.Print("Successfully written to wikis.yaml\n")
	absPath, err := filepath.Abs(filePath)
	if err != nil {
		return "", err
	}

	return absPath, nil
}

func ReadWikisYaml(filePath string) ([]string, []string, []string, error) {
	// Read the YAML file
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, nil, nil, err
	}

	// Parse the YAML data into a Wikis struct
	wikis := Wikis{}
	err = yaml.Unmarshal(data, &wikis)
	if err != nil {
		return nil, nil, nil, err
	}

	// Check if any wikis exist
	if len(wikis.Wikis) == 0 {
		return nil, nil, nil, fmt.Errorf("no wikis found in the YAML file")
	}

	// Extract IDs, server names, and paths from URLs
	ids := make([]string, 0, len(wikis.Wikis))
	serverNames := make([]string, 0, len(wikis.Wikis))
	paths := make([]string, 0, len(wikis.Wikis))
	for _, wiki := range wikis.Wikis {
		ids = append(ids, wiki.ID)

		// Split the URL at the first '/' to extract the server name and path
		parts := strings.SplitN(wiki.URL, "/", 2)
		serverName := parts[0]
		serverNames = append(serverNames, serverName)
		if len(parts) > 1 {
			paths = append(paths, "/"+parts[1])
		} else {
			paths = append(paths, "/")
		}
	}

	return ids, serverNames, paths, nil
}

// WikiIDExists checks if a wiki with the given wikiID exists in the installation
func WikiIDExists(installPath, wikiID string) (bool, error) {
	// Get the absolute path to the wikis.yaml file
	filePath := filepath.Join(installPath, "config", "wikis.yaml")

	// Check if the file exists
	if _, err := os.Stat(filePath); os.IsNotExist(err) {
		// File does not exist
		return false, nil
	}

	// Read the wikis from the YAML file
	ids, _, _, err := ReadWikisYaml(filePath)
	if err != nil {
		return false, err
	}

	// Check if a wiki with the given wikiID exists
	for _, id := range ids {
		if id == wikiID {
			return true, nil
		}
	}

	return false, nil
}

// WikiUrlExists checks if a wiki with the given URL (domain/path combo) exists in the installation
func WikiUrlExists(installPath, domain, wikiPath string) (bool, error) {
	// Get the absolute path to the wikis.yaml file
	filePath := filepath.Join(installPath, "config", "wikis.yaml")

	// Check if the file exists
	if _, err := os.Stat(filePath); os.IsNotExist(err) {
		// File does not exist
		return false, nil
	}

	// Read the wikis from the YAML file
	_, serverNames, paths, err := ReadWikisYaml(filePath)
	if err != nil {
		return false, err
	}

	// Check if a wiki with the given URL exists
	targetUrl := domain + "/" + wikiPath
	for i := range serverNames {
		if serverNames[i]+paths[i] == targetUrl {
			return true, nil
		}
	}

	return false, nil
}

func AddWiki(wikiID, installPath, domain, wikiPath, siteName string) error {
	// Get the absolute path to the wikis.yaml file
	filePath := filepath.Join(installPath, "config", "wikis.yaml")

	if siteName == "" {
		siteName = wikiID
	}
	// Read the existing wikis from the YAML file
	wikis := Wikis{}

	// Check if the file exists before trying to read it
	if _, err := os.Stat(filePath); err == nil {
		// File exists, read it
		data, err := os.ReadFile(filePath)
		if err != nil {
			return err
		}

		// Unmarshal the yaml file content into wikis
		err = yaml.Unmarshal(data, &wikis)
		if err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		// If the error is not because the file does not exist, return it
		return err
	}

	// Create a new wiki
	url := domain
	if wikiPath != "" {
		url = domain + "/" + wikiPath
	}
	newWiki := Wiki{ID: wikiID, URL: url, NAME: siteName}

	// Append the new wiki to the list of wikis
	wikis.Wikis = append(wikis.Wikis, newWiki)

	// Marshal the wikis back into YAML
	updatedData, err := yaml.Marshal(wikis)
	if err != nil {
		return err
	}

	// Write the updated data back to the file
	err = os.WriteFile(filePath, updatedData, permissions.FilePermission)
	if err != nil {
		return err
	}

	return nil
}

func RemoveWiki(wikiID, installPath string) error {
	// Get the absolute path to the wikis.yaml file
	filePath := filepath.Join(installPath, "config", "wikis.yaml")

	// Read the existing wikis from the YAML file
	wikis := Wikis{}
	data, err := os.ReadFile(filePath)
	if err != nil {
		return err
	}

	// Unmarshal the yaml file content into wikis
	err = yaml.Unmarshal(data, &wikis)
	if err != nil {
		return err
	}

	// Initialize an empty slice to store the remaining wikis
	remainingWikis := []Wiki{}

	// Find and remove the specified wiki
	for _, wiki := range wikis.Wikis {
		if wiki.ID != wikiID {
			remainingWikis = append(remainingWikis, wiki)
		}
	}

	// Check if all wikis were removed
	if len(remainingWikis) == 0 {
		return fmt.Errorf("cannot remove the last wiki in the Canasta Instance")
	}

	// Replace the existing wikis with the remaining wikis
	wikis.Wikis = remainingWikis

	// Marshal the updated wikis back into YAML
	updatedData, err := yaml.Marshal(wikis)
	if err != nil {
		return err
	}

	// Write the updated data back to the file
	err = os.WriteFile(filePath, updatedData, permissions.FilePermission)
	if err != nil {
		return err
	}

	return nil
}
