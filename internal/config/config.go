package config

import (
	"encoding/json"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"sync"
	"text/tabwriter"

	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/perms"
	"github.com/kirsle/configdir"
)

type Installation struct {
	Id           string `json:"id"`
	Path         string `json:"path"`
	Orchestrator string `json:"orchestrator"`
	DevMode      bool   `json:"devMode,omitempty"`
	ManagedCluster bool   `json:"managedCluster,omitempty"`
	Registry     string `json:"registry,omitempty"`
	KindCluster  string `json:"kindCluster,omitempty"`
	BuildFrom    string `json:"buildFrom,omitempty"`
}

type Orchestrator struct {
	Id, Path string
}

type Canasta struct {
	Orchestrators map[string]Orchestrator
	Installations map[string]Installation
}

var (
	directory             string
	confFile              string
	existingInstallations Canasta
	initOnce              sync.Once
	initErr               error
	configDirOverride     string
)

func ensureInitialized() error {
	initOnce.Do(func() {
		var dir string
		var err error
		if configDirOverride != "" {
			dir = configDirOverride
		} else {
			dir, err = GetConfigDir()
			if err != nil {
				initErr = err
				return
			}
		}
		directory = dir
		confFile = filepath.Join(directory, "conf.json")

		// Check for the conf.json file
		_, err = os.Stat(confFile)
		if os.IsNotExist(err) {
			// Creating conf.json
			if err := write(Canasta{Installations: map[string]Installation{}, Orchestrators: map[string]Orchestrator{}}); err != nil {
				initErr = err
				return
			}
		} else if err != nil {
			initErr = fmt.Errorf("error statting %s (%w)", confFile, err)
			return
		}

		// Check if the file is writable
		f, err := os.OpenFile(confFile, os.O_WRONLY, 0)
		if err != nil {
			initErr = err
			return
		}
		f.Close()

		// Update the existingInstallations list
		if err := read(&existingInstallations); err != nil {
			initErr = err
			return
		}
	})
	return initErr
}

// ResetForTesting resets package state so tests can use a custom config directory.
// After calling this, the next call to any public function will re-initialize
// using the provided directory instead of the system config directory.
func ResetForTesting(dir string) {
	initOnce = sync.Once{}
	initErr = nil
	configDirOverride = dir
	directory = ""
	confFile = ""
	existingInstallations = Canasta{}
}

func Exists(canastaID string) (bool, error) {
	if err := ensureInitialized(); err != nil {
		return false, err
	}
	err := read(&existingInstallations)
	if err != nil {
		return false, err
	}
	return existingInstallations.Installations[canastaID].Id != "", nil
}

func OrchestratorExists(orchestrator string) (bool, error) {
	if err := ensureInitialized(); err != nil {
		return false, err
	}
	err := read(&existingInstallations)
	if err != nil {
		return false, err
	}
	return existingInstallations.Orchestrators[orchestrator].Path != "", nil
}

func ListAll() error {
	if err := ensureInitialized(); err != nil {
		return err
	}
	err := read(&existingInstallations)
	if err != nil {
		return err
	}

	if len(existingInstallations.Installations) == 0 {
		fmt.Println("No instances found.")
		return nil
	}

	writer := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(writer, "Canasta ID\tWiki ID\tServer Name\tServer Path\tInstallation Path\tOrchestrator")

	for _, installation := range existingInstallations.Installations {
		installPath := installation.Path
		pathMissing := false
		if _, err := os.Stat(installation.Path); os.IsNotExist(err) {
			installPath = installation.Path + " [not found]"
			pathMissing = true
		}

		if pathMissing {
			fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
				installation.Id,
				"N/A",
				"N/A",
				"N/A",
				installPath,
				installation.Orchestrator)
			continue
		}

		if _, err := os.Stat(installation.Path + "/config/wikis.yaml"); os.IsNotExist(err) {
			// File does not exist, print only installation info
			fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
				installation.Id,
				"N/A", // Placeholder
				"N/A", // Placeholder
				"N/A", // Placeholder
				installPath,
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
					installPath,
					installation.Orchestrator)
			} else {
				fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
					"-",
					ids[i],
					serverNames[i],
					paths[i],
					installPath,
					installation.Orchestrator)
			}

		}
	}
	writer.Flush()
	return nil
}

func GetAll() (map[string]Installation, error) {
	if err := ensureInitialized(); err != nil {
		return nil, err
	}
	err := read(&existingInstallations)
	if err != nil {
		return nil, err
	}
	return existingInstallations.Installations, nil
}

func GetDetails(canastaID string) (Installation, error) {
	if err := ensureInitialized(); err != nil {
		return Installation{}, err
	}
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
	if err := ensureInitialized(); err != nil {
		return "", err
	}
	var canastaID string
	for _, installations := range existingInstallations.Installations {
		if installations.Path == installPath {
			return installations.Id, nil
		}
	}
	return canastaID, fmt.Errorf("No Canasta installations exist at %s", installPath)
}

func Add(details Installation) error {
	if err := ensureInitialized(); err != nil {
		return err
	}
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
	if err := ensureInitialized(); err != nil {
		return err
	}
	if existingInstallations.Orchestrators == nil {
		existingInstallations.Orchestrators = map[string]Orchestrator{}
	}
	supportedOrchestrators := map[string]bool{
		"compose":    true,
		"kubernetes": true,
		"k8s":        true,
	}
	if !supportedOrchestrators[details.Id] {
		return fmt.Errorf("orchestrator %s is not supported", details.Id)
	}
	existingInstallations.Orchestrators[details.Id] = details
	err := write(existingInstallations)
	return err
}

func GetOrchestrator(orchestrator string) (Orchestrator, error) {
	if err := ensureInitialized(); err != nil {
		return Orchestrator{}, err
	}
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
	if err := ensureInitialized(); err != nil {
		return err
	}
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
	if err := ensureInitialized(); err != nil {
		return err
	}
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
	return os.WriteFile(confFile, file, perms.FilePerm)
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
		dir = "/etc/canasta"
	}

	fi, err := os.Stat(dir)
	if os.IsNotExist(err) {
		err = os.MkdirAll(dir, 0o755)
		if err != nil {
			return "", err
		}
	} else if err != nil {
		return "", fmt.Errorf("error statting %s (%w)", dir, err)
	} else {
		mode := fi.Mode()
		if !mode.IsDir() {
			return "", fmt.Errorf("%s exists but is not a directory", dir)
		}
	}

	return dir, nil
}

