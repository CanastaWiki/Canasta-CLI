package main

import (
	"fmt"
	"log"
	"os"
)

var (
	directory string
	confFile  string
)

func init() {

	log.SetFlags(0)
	log.SetPrefix("Error: ")
	directory = "/etc/canasta"
	confFile = directory + "/confFile.js"

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
		_, err := os.Create(confFile)
		if err != nil {
			log.Fatal(err)
		}
	}
}

func write() error {

	return nil
}

func main() {

}
