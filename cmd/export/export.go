package cmd

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

var (
	pwd            string
	err            error
	instance       config.Installation
	wikiName       string
	outputFilePath string
)

// NewCmdExport exports the database.
func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var wikiName string
	var outputFilePath string

	pwd, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}

	exportCmd := &cobra.Command{
		Use:   "export",
		Short: "Export a database from a Canasta instance",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Printf("Exporting database for wiki '%s' from Canasta instance '%s'...\n", wikiName, instance.Id)
			err := ExportDatabase(wikiName, outputFilePath, instance)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Done.")
			return nil
		},
	}

	exportCmd.Flags().StringVarP(&wikiName, "wiki", "w", "", "ID of the wiki (database) to export")
	exportCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	exportCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	exportCmd.Flags().StringVarP(&outputFilePath, "output", "o", "", "Output file path for the exported database")

	return exportCmd
}

// ExportDatabase exports a database from a Canasta instance.
func ExportDatabase(databaseName, outputFilePath string, instance config.Installation) error {

	//Checking Installation existence
	instance, err = canasta.CheckCanastaId(instance)
	if err != nil {
		return err
	}

	//Checking Running status
	err = orchestrators.CheckRunningStatus(instance.Path, instance.Id, instance.Orchestrator)
	if err != nil {
		return err
	}

	// Exporting the database
	err = orchestrators.ExportDatabase(instance.Path, instance.Orchestrator, databaseName, outputFilePath)
	if err != nil {
		return err
	}
   
	return nil
}
