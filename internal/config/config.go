package config

import (
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"os"
	"os/exec"
	"os/user"
	"path"
	"syscall"

	"github.com/kirsle/configdir"
)

type Installation struct {
	Id, Path, Orchestrator string
}

type Canasta struct {
	Installations map[string]Installation
}

var (
	directory             string = "/etc/canasta"
	confFile              string = "conf.json"
	existingInstallations Canasta
)

func Exists(canastaId string) bool {
	err := read(&existingInstallations)
	if err != nil {
		log.Fatal(err)
	}
	return existingInstallations.Installations[canastaId].Id != ""
}

func ListAll() {
	err := read(&existingInstallations)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("Canasta ID\tInstallation Path\t\t\t\t\tOrchestrator\n\n")
	for _, installation := range existingInstallations.Installations {
		fmt.Printf("%s\t%s\t%s\n", installation.Id, installation.Path, installation.Orchestrator)
	}
}

func GetDetails(canastaId string) (Installation, error) {
	if Exists(canastaId) {
		return existingInstallations.Installations[canastaId], nil
	}
	return Installation{}, fmt.Errorf("Canasta installation with the ID doesn't exist")
}

func GetCanastaId(path string) (string, error) {
	var canastaId string
	for _, installations := range existingInstallations.Installations {
		if installations.Path == path {
			return installations.Id, nil
		}
	}
	return canastaId, fmt.Errorf("No canasta installations exist at %s", path)
}

func Add(details Installation) error {
	if Exists(details.Id) {
		return fmt.Errorf("Canasta ID is already used for another installation")
	} else {
		existingInstallations.Installations[details.Id] = details
	}
	err := write(existingInstallations)
	return err
}

func Delete(canastaID string) error {
	if Exists(canastaID) {
		delete(existingInstallations.Installations, canastaID)
	} else {
		log.Fatal(fmt.Errorf("Canasta installation with the ID doesn't exist"))
	}
	if err := write(existingInstallations); err != nil {
		log.Fatal(err)
	}

	return nil
}

func write(details Canasta) error {
	file, err := json.MarshalIndent(details, "", "	")
	if err != nil {
		log.Fatal(err)
	}
	return ioutil.WriteFile(confFile, file, 0644)
}

func read(details *Canasta) error {
	data, err := os.ReadFile(confFile)
	if err != nil {
		log.Fatal(err)
	}
	err = json.Unmarshal(data, details)
	return err
}

func GetConfigDir() string {
	dir := configdir.LocalConfig("canasta")
	exists := false
	
	// Checks if this is running as root
	currentUser, err := user.Current()
	if err != nil {
		errReport := fmt.Errorf("Unable to get the current user: %s", err)
		log.Fatal(errReport)
	}

	if currentUser.Username == "root" {
		dir = directory
	} else if currentUser.Username != "root" {
		fi, err := os.Stat(dir)
		if os.IsNotExist(err) {
			var configDir string = ".config"
			log.Print(fmt.Sprintf("Creating %s\n", configDir))
			err = os.Mkdir(configDir, os.ModePerm)
			if err != nil {
				log.Fatal(err)
			}
		} else if err != nil {
			msg := fmt.Sprintf("error statting %s (%s)", dir, err)
			log.Print(msg)
		} else {
			mode := fi.Mode()
			if mode.IsDir() {
				exists = true
			}
		}

		if exists != true {
			msg := fmt.Sprintf("Using %s for configuration...", dir)
			log.Print(msg)
		}
	}

	return dir
}

func init() {
	directory = GetConfigDir()
	confFile = path.Join(directory, confFile)

	_, err := exec.LookPath("docker-compose")
	if err != nil {
		log.Fatal(fmt.Errorf("docker-compose should be installed! (%s)", err))
	}

	// Checks for the conf.json file
	_, err = os.Stat(confFile)
	if os.IsNotExist(err) {
		// Check for the configuration folder
		_, err = os.Stat(directory)
		// Creating configuration folder
		if os.IsNotExist(err) {
			log.Print(fmt.Sprintf("Creating %s\n", directory))
			err = os.Mkdir(directory, os.ModePerm)
			if err != nil {
				log.Fatal(err)
			}
		} else if err != nil {
			log.Fatal(err)
		}
		// Creating conf.json
		err := write(Canasta{Installations: map[string]Installation{}})
		if err != nil {
			log.Fatal(err)
		}
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
