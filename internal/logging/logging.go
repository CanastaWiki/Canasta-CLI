package logging

import (
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"os"
	"syscall"
)

type Installation struct {
	Id, Path, Orchestrator string
}

type Canasta struct {
	Installations map[string]Installation
}

var (
	directory             string
	confFile              string
	verbose               bool
	existingInstallations Canasta
)

func Exists(canastaId string) bool {
	err := read(&existingInstallations)
	if err != nil {
		Fatal(err)
	}
	return existingInstallations.Installations[canastaId].Id != ""
}

func ListAll() {
	err := read(&existingInstallations)
	if err != nil {
		Fatal(err)
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
		Fatal(fmt.Errorf("Canasta installation with the ID doesn't exist"))
	}
	if err := write(existingInstallations); err != nil {
		Fatal(err)
	}

	return nil
}

func write(details Canasta) error {
	file, err := json.MarshalIndent(details, "", "	")
	if err != nil {
		Fatal(err)
	}
	return ioutil.WriteFile(confFile, file, 0644)
}

func read(details *Canasta) error {
	data, err := os.ReadFile(confFile)
	if err != nil {
		Fatal(err)
	}
	err = json.Unmarshal(data, details)
	return err
}

func SetVerbose(v bool) {
	verbose = v
}

func Print(output string) {
	log.SetFlags(0)
	if verbose {
		log.Print(output)
	}
}

func Fatal(err error) {
	log.SetFlags(0)
	log.Fatal(err)
}

func init() {

	directory = "/etc/canasta"
	confFile = directory + "/conf.json"

	// Checks for the conf.json file
	_, err := os.Stat(confFile)
	if os.IsNotExist(err) {
		// Check for the configuration folder
		_, err = os.Stat(directory)
		// Creating configuration folder
		if os.IsNotExist(err) {
			Print(fmt.Sprintf("Creating %s\n", directory))
			err = os.Mkdir(directory, os.ModePerm)
			if err != nil {
				Fatal(err)
			}
		} else if err != nil {
			Fatal(err)
		}
		// Creating conf.json
		err := write(Canasta{Installations: map[string]Installation{}})
		if err != nil {
			Fatal(err)
		}
	}
	// Check if the file is writable/has enough permissions
	err = syscall.Access(confFile, syscall.O_RDWR)
	if err != nil {
		Fatal(err)
	}
}
