package farmsettings

import (
	"fmt"
	"io/ioutil"
	"os"
	"path/filepath"
	"strings"

	yaml "gopkg.in/yaml.v2"
)

type Wiki struct {
	ID   string `yaml:"id"`
	URL  string `yaml:"url"`
	NAME string `yaml:"name"`
}

type Wikis struct {
	Wikis []Wiki `yaml:"wikis"`
}

// ValidateWikiID validates that a wiki ID doesn't contain invalid characters or reserved names
func ValidateWikiID(id string) error {
	// Check if the ID contains a hyphen (-)
	if strings.Contains(id, "-") {
		return fmt.Errorf("The character '-' is not allowed in WikiID")
	}

	// Check if the ID is one of the reserved names
	reservedNames := []string{"settings", "images", "w", "wiki"}
	for _, name := range reservedNames {
		if id == name {
			return fmt.Errorf("%s cannot be used as WikiID", id)
		}
	}

	// If it passes the checks, return nil (no error)
	return nil
}

func CreateYaml(id, domain, siteName string, path *string) error {
	if *path == "" {
		var err error
		*path, err = GenerateWikisYaml("./wikis.yaml", id, domain, siteName)
		if err != nil {
			return err
		}
	}

	return nil
}

func GenerateWikisYaml(filePath, id, domain, siteName string) (string, error) {
	if siteName == "" {
		siteName = id
	}
	wikis := Wikis{}
	wikis.Wikis = append(wikis.Wikis, Wiki{ID: id, URL: domain, NAME: siteName})

	out, err := yaml.Marshal(&wikis)
	if err != nil {
		return "", err
	}

	err = ioutil.WriteFile(filePath, out, 0644)
	if err != nil {
		return "", err
	}

	fmt.Println("Successfully written to wikis.yaml")
	absPath, err := filepath.Abs(filePath)
	if err != nil {
		return "", err
	}

	return absPath, nil
}

func ReadWikisYaml(filePath string) ([]string, []string, []string, error) {
	// Read the YAML file
	data, err := ioutil.ReadFile(filePath)
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

// ReadWikisYamlWithNames reads the wikis.yaml file and returns the full Wiki structs with IDs, URLs, and site names
func ReadWikisYamlWithNames(filePath string) ([]Wiki, error) {
	// Read the YAML file
	data, err := ioutil.ReadFile(filePath)
	if err != nil {
		return nil, err
	}

	// Parse the YAML data into a Wikis struct
	wikis := Wikis{}
	err = yaml.Unmarshal(data, &wikis)
	if err != nil {
		return nil, err
	}

	// Check if any wikis exist
	if len(wikis.Wikis) == 0 {
		return nil, fmt.Errorf("no wikis found in the YAML file")
	}

	return wikis.Wikis, nil
}

// WikiIDExists checks if a wiki with the given ID exists in the installation
func WikiIDExists(installPath, id string) (bool, error) {
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

	// Check if a wiki with the given ID exists
	for _, wikiID := range ids {
		if wikiID == id {
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

func AddWiki(id, installPath, domain, wikiPath, siteName string) error {
	// Get the absolute path to the wikis.yaml file
	filePath := filepath.Join(installPath, "config", "wikis.yaml")

	if siteName == "" {
		siteName = id
	}
	// Read the existing wikis from the YAML file
	wikis := Wikis{}

	// Check if the file exists before trying to read it
	if _, err := os.Stat(filePath); err == nil {
		// File exists, read it
		data, err := ioutil.ReadFile(filePath)
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
	newWiki := Wiki{ID: id, URL: filepath.Join(domain, wikiPath), NAME: siteName}

	// Append the new wiki to the list of wikis
	wikis.Wikis = append(wikis.Wikis, newWiki)

	// Marshal the wikis back into YAML
	updatedData, err := yaml.Marshal(wikis)
	if err != nil {
		return err
	}

	// Write the updated data back to the file
	err = ioutil.WriteFile(filePath, updatedData, 0644)
	if err != nil {
		return err
	}

	return nil
}

func RemoveWiki(id, installPath string) error {
	// Get the absolute path to the wikis.yaml file
	filePath := filepath.Join(installPath, "config", "wikis.yaml")

	// Read the existing wikis from the YAML file
	wikis := Wikis{}
	data, err := ioutil.ReadFile(filePath)
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
		if wiki.ID != id {
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
	err = ioutil.WriteFile(filePath, updatedData, 0644)
	if err != nil {
		return err
	}

	return nil
}
