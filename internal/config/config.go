package config

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"sync"
	"text/tabwriter"

	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
)

type Instance struct {
	ID             string `json:"id"`
	Path           string `json:"path"`
	Orchestrator   string `json:"orchestrator"`
	DevMode        bool   `json:"devMode,omitempty"`
	ManagedCluster bool   `json:"managedCluster,omitempty"`
	Registry       string `json:"registry,omitempty"`
	KindCluster    string `json:"kindCluster,omitempty"`
	BuildFrom      string `json:"buildFrom,omitempty"`
}

type Canasta struct {
	Instances           map[string]Instance `json:"Instances"`
	LegacyInstallations map[string]Instance `json:"Installations,omitempty"`
}

var (
	directory         string
	confFile          string
	existingInstances Canasta
	initOnce          sync.Once
	initErr           error
	configDirOverride string
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
			if err := write(Canasta{Instances: map[string]Instance{}}); err != nil {
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

		// Update the existingInstances list
		if err := read(&existingInstances); err != nil {
			initErr = err
			return
		}

		// Migrate legacy conf.json that used "Installations" key
		if len(existingInstances.Instances) == 0 && len(existingInstances.LegacyInstallations) > 0 {
			existingInstances.Instances = existingInstances.LegacyInstallations
			existingInstances.LegacyInstallations = nil
			if err := write(existingInstances); err != nil {
				initErr = err
				return
			}
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
	existingInstances = Canasta{}
}

func Exists(canastaID string) (bool, error) {
	if err := ensureInitialized(); err != nil {
		return false, err
	}
	err := read(&existingInstances)
	if err != nil {
		return false, err
	}
	return existingInstances.Instances[canastaID].ID != "", nil
}

func ListAll(w io.Writer) error {
	if err := ensureInitialized(); err != nil {
		return err
	}
	err := read(&existingInstances)
	if err != nil {
		return err
	}

	if len(existingInstances.Instances) == 0 {
		fmt.Fprintf(w, "No instances found (looked in %s)\n", confFile)
		if IsRunningAsRoot() {
			fmt.Fprintln(w, "Note: Running as root uses /etc/canasta/conf.json. Instances")
			fmt.Fprintln(w, "registered by a non-root user are stored in ~/.config/canasta/conf.json.")
		}

		return nil
	}

	writer := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(writer, "Canasta ID\tWiki ID\tServer Name\tServer Path\tInstance Path\tOrchestrator")

	for _, instance := range existingInstances.Instances {
		installPath := instance.Path
		pathMissing := false
		if _, err := os.Stat(instance.Path); os.IsNotExist(err) {
			installPath = instance.Path + " [not found]"
			pathMissing = true
		}

		if pathMissing {
			fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
				instance.ID,
				"N/A",
				"N/A",
				"N/A",
				installPath,
				instance.Orchestrator)
			continue
		}

		if _, err := os.Stat(filepath.Join(instance.Path, "config", "wikis.yaml")); os.IsNotExist(err) {
			// File does not exist, print only instance info
			fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
				instance.ID,
				"N/A", // Placeholder
				"N/A", // Placeholder
				"N/A", // Placeholder
				installPath,
				instance.Orchestrator)
			continue
		}

		ids, serverNames, paths, err := farmsettings.ReadWikisYaml(filepath.Join(instance.Path, "config", "wikis.yaml"))
		if err != nil {
			fmt.Printf("Error reading wikis.yaml for instance ID '%s': %s\n", instance.ID, err)
			continue
		}

		for i := range ids {
			if i == 0 {
				fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
					instance.ID,
					ids[i],
					serverNames[i],
					paths[i],
					installPath,
					instance.Orchestrator)
			} else {
				fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n",
					"-",
					ids[i],
					serverNames[i],
					paths[i],
					installPath,
					instance.Orchestrator)
			}

		}
	}
	writer.Flush()
	return nil
}

func GetAll() (map[string]Instance, error) {
	if err := ensureInitialized(); err != nil {
		return nil, err
	}
	err := read(&existingInstances)
	if err != nil {
		return nil, err
	}
	return existingInstances.Instances, nil
}

// GetConfFilePath returns the path to the conf.json file currently in use.
// Must be called after GetAll or any other function that triggers initialization.
func GetConfFilePath() string {
	return confFile
}

// IsRunningAsRoot returns true if the current process is running as root.
func IsRunningAsRoot() bool {
	currentUser, err := user.Current()
	if err != nil {
		return false
	}
	return currentUser.Username == "root"
}

func GetDetails(canastaID string) (Instance, error) {
	if err := ensureInitialized(); err != nil {
		return Instance{}, err
	}
	exists, err := Exists(canastaID)
	if err != nil {
		return Instance{}, err
	}
	if exists {
		return existingInstances.Instances[canastaID], nil
	}
	return Instance{}, fmt.Errorf("canasta instance with the ID doesn't exist")
}

func GetCanastaID(installPath string) (string, error) {
	if err := ensureInitialized(); err != nil {
		return "", err
	}
	// Walk up the directory tree to find an enclosing instance.
	dir := installPath
	for {
		for _, inst := range existingInstances.Instances {
			if inst.Path == dir {
				return inst.ID, nil
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return "", fmt.Errorf("no Canasta instances exist at %s", installPath)
}

func Add(details Instance) error {
	if err := ensureInitialized(); err != nil {
		return err
	}
	exists, err := Exists(details.ID)
	if err != nil {
		return err
	}
	if exists {
		return fmt.Errorf("canasta ID is already used for another instance")
	}
	if existingInstances.Instances == nil {
		existingInstances.Instances = map[string]Instance{}
	}
	existingInstances.Instances[details.ID] = details
	return write(existingInstances)
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
		return fmt.Errorf("canasta instance with the ID doesn't exist")
	}
	delete(existingInstances.Instances, canastaID)
	return write(existingInstances)
}

// Update updates an existing instance's configuration
func Update(details Instance) error {
	if err := ensureInitialized(); err != nil {
		return err
	}
	exists, err := Exists(details.ID)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("canasta instance with ID '%s' doesn't exist", details.ID)
	}
	existingInstances.Instances[details.ID] = details
	return write(existingInstances)
}

func write(details Canasta) error {
	file, err := json.MarshalIndent(details, "", "	")
	if err != nil {
		return err
	}
	return os.WriteFile(confFile, file, permissions.FilePermission)
}

func read(details *Canasta) error {
	data, err := os.ReadFile(confFile)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, details)
}

func GetConfigDir() (string, error) {
	var dir string

	if envDir := os.Getenv("CANASTA_CONFIG_DIR"); envDir != "" {
		dir = envDir
	} else {
		base, err := os.UserConfigDir()
		if err != nil {
			return "", fmt.Errorf("unable to determine config directory: %w", err)
		}
		dir = filepath.Join(base, "canasta")

		// Checks if this is running as root
		if IsRunningAsRoot() {
			dir = "/etc/canasta"
		}
	}

	fi, err := os.Stat(dir)
	switch {
	case os.IsNotExist(err):
		err = os.MkdirAll(dir, 0o755)
		if err != nil {
			return "", err
		}
	case err != nil:
		return "", fmt.Errorf("error statting %s (%w)", dir, err)
	default:
		mode := fi.Mode()
		if !mode.IsDir() {
			return "", fmt.Errorf("%s exists but is not a directory", dir)
		}
	}

	return dir, nil
}
