package config

import (
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"os"
	"os/user"
	"path"
	"syscall"
	"text/tabwriter"

	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/kirsle/configdir"
)

type Installation struct {
	Id           string `json:"id"`
	Path         string `json:"path"`
	Orchestrator string `json:"orchestrator"`
	DevMode      bool   `json:"devMode,omitempty"`
	LocalStack   bool   `json:"localStack,omitempty"`
}

type Orchestrator struct {
	Id, Path string
}

type Canasta struct {
	Orchestrators map[string]Orchestrator
	Installations map[string]Installation
}

var (
	directory             string = "/etc/canasta"
	confFile              string = "conf.json"
	existingInstallations Canasta
)

func Exists(canastaID string) (bool, error) {
	err := read(&existingInstallations)
	if err != nil {
		return false, err
	}
	return existingInstallations.Installations[canastaID].Id != "", nil
}

func OrchestratorExists(orchestrator string) (bool, error) {
	err := read(&existingInstallations)
	if err != nil {
		return false, err
	}
	return existingInstallations.Orchestrators[orchestrator].Path != "", nil
}

func ListAll() error {
	err := read(&existingInstallations)
	if err != nil {
		return err
	}

	writer := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(writer, "Canasta ID\tWiki ID\tServer Name\tServer Path\tInstallation Path\tOrchestrator")

	for _, installation := range existingInstallations.Installations {
		if _, err := os.Stat(installation.Path + "/config/wikis.yaml"); os.IsNotExist(err) {
			// File does not exist, print only installation info
			fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
				installation.Id,
				"N/A", // Placeholder
				"N/A", // Placeholder
				"N/A", // Placeholder
				installation.Path,
				installation.Orchestrator)
			continue
		}

		ids, serverNames, paths, err := farmsettings.ReadWikisYaml(installation.Path + "/config/wikis.yaml")
		if err != nil {
			fmt.Printf("Error reading wikis.yaml for installation ID '%s': %s\n", installation.Id, err)
			continue
		}

		for i := range ids {
			if i == 0 {
				fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
					installation.Id,
					ids[i],
					serverNames[i],
					paths[i],
					installation.Path,
					installation.Orchestrator)
			} else {
				fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
					"-",
					ids[i],
					serverNames[i],
					paths[i],
					installation.Path,
					installation.Orchestrator)
			}

		}
	}
	writer.Flush()
	return nil
}

func GetAll() (map[string]Installation, error) {
	err := read(&existingInstallations)
	if err != nil {
		return nil, err
	}
	return existingInstallations.Installations, nil
}

func GetDetails(canastaID string) (Installation, error) {
	exists, err := Exists(canastaID)
	if err != nil {
		return Installation{}, err
	}
	if exists {
		return existingInstallations.Installations[canastaID], nil
	}
	return Installation{}, fmt.Errorf("Canasta installation with the ID doesn't exist")
}

func GetCanastaID(installPath string) (string, error) {
	var canastaID string
	for _, installations := range existingInstallations.Installations {
		if installations.Path == installPath {
			return installations.Id, nil
		}
	}
	return canastaID, fmt.Errorf("No Canasta installations exist at %s", installPath)
}

func Add(details Installation) error {
	exists, err := Exists(details.Id)
	if err != nil {
		return err
	}
	if exists {
		return fmt.Errorf("Canasta ID is already used for another installation")
	}
	existingInstallations.Installations[details.Id] = details
	return write(existingInstallations)
}

func AddOrchestrator(details Orchestrator) error {
	if existingInstallations.Orchestrators == nil {
		existingInstallations.Orchestrators = map[string]Orchestrator{}
	}
	if details.Id != "compose" {
		return fmt.Errorf("orchestrator %s is not suported", details.Id)
	}
	existingInstallations.Orchestrators[details.Id] = details
	err := write(existingInstallations)
	return err
}

func GetOrchestrator(orchestrator string) (Orchestrator, error) {
	exists, err := OrchestratorExists(orchestrator)
	if err != nil {
		return Orchestrator{}, err
	}
	if exists {
		return existingInstallations.Orchestrators[orchestrator], nil
	}
	return Orchestrator{}, nil
}

func Delete(canastaID string) error {
	exists, err := Exists(canastaID)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("Canasta installation with the ID doesn't exist")
	}
	delete(existingInstallations.Installations, canastaID)
	return write(existingInstallations)
}

// Update updates an existing installation's configuration
func Update(details Installation) error {
	exists, err := Exists(details.Id)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("Canasta installation with ID '%s' doesn't exist", details.Id)
	}
	existingInstallations.Installations[details.Id] = details
	return write(existingInstallations)
}

func write(details Canasta) error {
	file, err := json.MarshalIndent(details, "", "	")
	if err != nil {
		return err
	}
	return ioutil.WriteFile(confFile, file, 0644)
}

func read(details *Canasta) error {
	data, err := os.ReadFile(confFile)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, details)
}

func GetConfigDir() (string, error) {
	dir := configdir.LocalConfig("canasta")

	// Checks if this is running as root
	currentUser, err := user.Current()
	if err != nil {
		return "", fmt.Errorf("Unable to get the current user: %s", err)
	}

	if currentUser.Username == "root" {
		dir = directory
	}

	fi, err := os.Stat(dir)
	if os.IsNotExist(err) {
		err = os.MkdirAll(dir, 0o755)
		if err != nil {
			return "", err
		}
	} else if err != nil {
		return "", fmt.Errorf("error statting %s (%s)", dir, err)
	} else {
		mode := fi.Mode()
		if !mode.IsDir() {
			return "", fmt.Errorf("%s exists but is not a directory", dir)
		}
	}

	return dir, nil
}

func init() {
	var err error
	directory, err = GetConfigDir()
	if err != nil {
		log.Fatal(err)
	}
	confFile = path.Join(directory, confFile)

	// Checks for the conf.json file
	_, err = os.Stat(confFile)
	if os.IsNotExist(err) {
		// Creating conf.json
		if err := write(Canasta{Installations: map[string]Installation{}, Orchestrators: map[string]Orchestrator{}}); err != nil {
			log.Fatal(err)
		}
	} else if err != nil {
		log.Fatalf("error statting %s (%s)", confFile, err)
	}

	// Check if the file is writable/has enough permissions
	if err = syscall.Access(confFile, syscall.O_RDWR); err != nil {
		log.Fatal(err)
	}

	// Update the existingInstallations list
	if err := read(&existingInstallations); err != nil {
		log.Fatal(err)
	}
}
