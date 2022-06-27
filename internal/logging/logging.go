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
	return Installation{}, fmt.Errorf("Canasta Installation with the ID doesn't exist")
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
		return fmt.Errorf("Canasta Installation with the ID doesn't exist")
	}
	err := write(existingInstallations)
	return err
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
	err = json.Unmarshal(data, details)
	return err
}

func init() {

	directory = "/etc/canasta"
	confFile = directory + "/conf.js"

	//Checks for the conf.js file
	_, err := os.Stat(confFile)
	if os.IsNotExist(err) {
		//Check for the configuration folder
		_, err = os.Stat(directory)
		//Creating configuration folder
		if os.IsNotExist(err) {
			fmt.Println("Creating", directory)
			err = os.Mkdir(directory, os.ModePerm)
			if err != nil {
				log.Fatal(err)
			}
		} else if err != nil {
			log.Fatal(err)
		}
		//Creating confFile.js
		err := write(Canasta{Installations: map[string]Installation{}})
		if err != nil {
			log.Fatal(err)
		}
	}
	// Check if the file is writable/ has enough permissions
	err = syscall.Access(confFile, syscall.O_RDWR)
	if err != nil {
		log.Fatal(err)
	}
}
