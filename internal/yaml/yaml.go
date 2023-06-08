package yaml

import (
	"fmt"
	"io/ioutil"
	"path/filepath"
	"strings"

	yaml "gopkg.in/yaml.v2"
)

type Wiki struct {
	ID  string `yaml:"id"`
	URL string `yaml:"url"`
}

type Wikis struct {
	Wikis []Wiki `yaml:"wikis"`
}

func ParseYaml(name, domain string, path *string) error {
	if *path == "" {
		var err error
		*path, err = GenerateWikisYaml(name, domain)
		if err != nil {
			return err
		}
	}

	return nil
}

func GenerateWikisYaml(name, domain string) (string, error) {
	wikis := Wikis{}
	wikis.Wikis = append(wikis.Wikis, Wiki{ID: name, URL: domain})

	out, err := yaml.Marshal(&wikis)
	if err != nil {
		return "", err
	}

	filePath := "./wikis.yaml"
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

func CheckWiki(path, name string) error {
	// Get the absolute path to the wikis.yaml file
	filePath := filepath.Join(path, "config", "wikis.yaml")
	
	// Read the wikis from the YAML file
	ids, _, _, err := ReadWikisYaml(filePath)
	if err != nil {
		return err
	}

	// Check if a wiki with the given name exists
	for _, id := range ids {
		if id == name {
			return fmt.Errorf("A wiki with the name '%s' already exists", name)
		}
	}

	return nil
}

func AddWiki(name, path, domain, wikipath string) error {
    // Get the absolute path to the wikis.yaml file
    filePath := filepath.Join(path, "config", "wikis.yaml")
    
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
  
    // Create a new wiki
    newWiki := Wiki{
        ID:  name,
        URL: filepath.Join(domain,wikipath),
    }
    
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