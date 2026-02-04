package importcmd

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/cmd/restart"
	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var wikiID string
	var databasePath string
	var settingsPath string

	importCmd := &cobra.Command{
		Use:   "import",
		Short: "Import a database into an existing wiki",
		Long: `Import a database dump into an existing wiki, replacing its current database.
The instance must be running. Supported formats are .sql and .sql.gz files.

To create a new wiki from a database dump, use the --database flag with
'canasta create' or 'canasta add' instead.`,
		Example: `  # Import a SQL dump into a wiki
  canasta import -i myinstance -w main -d /path/to/dump.sql

  # Import a gzipped dump and replace the wiki's Settings.php
  canasta import -i myinstance -w main -d /path/to/dump.sql.gz -l /path/to/Settings.php`,
		RunE: func(cmd *cobra.Command, args []string) error {
			var err error

			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				log.Fatal(err)
			}

			// Check containers are running
			err = orchestrators.CheckRunningStatus(instance)
			if err != nil {
				log.Fatal(err)
			}

			// Verify the wiki exists
			exists, err := farmsettings.WikiIDExists(instance.Path, wikiID)
			if err != nil {
				log.Fatal(err)
			}
			if !exists {
				log.Fatal(fmt.Errorf("wiki '%s' does not exist in Canasta instance '%s'", wikiID, instance.Id))
			}

			// Validate database path
			if err := canasta.ValidateDatabasePath(databasePath); err != nil {
				log.Fatal(err)
			}

			workingDir, err := os.Getwd()
			if err != nil {
				log.Fatal(err)
			}

			fmt.Printf("Importing database into wiki '%s' in Canasta instance '%s'...\n", wikiID, instance.Id)
			if err := importDatabase(instance, wikiID, databasePath, settingsPath, workingDir); err != nil {
				log.Fatal(err)
			}
			fmt.Println("Done.")
			return nil
		},
	}

	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	importCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	importCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "ID of the wiki to import into")
	importCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to SQL dump file (.sql or .sql.gz)")
	importCmd.Flags().StringVarP(&settingsPath, "wiki-settings", "l", "", "Path to per-wiki Settings.php to replace the existing one")

	importCmd.MarkFlagRequired("wiki")
	importCmd.MarkFlagRequired("database")

	return importCmd
}

func importDatabase(instance config.Installation, wikiID, databasePath, settingsPath, workingDir string) error {
	// Read database password from .env
	envVariables := canasta.GetEnvVariable(instance.Path + "/.env")
	dbPassword := envVariables["MYSQL_PASSWORD"]

	// Import the database
	err := orchestrators.ImportDatabase(wikiID, databasePath, dbPassword, instance)
	if err != nil {
		return err
	}

	// If settings file provided, copy it to the wiki's config directory
	if settingsPath != "" {
		err = canasta.CopyWikiSettingFile(instance.Path, wikiID, settingsPath, workingDir)
		if err != nil {
			return err
		}
	}

	// Restart containers to apply changes
	err = restart.Restart(instance, false, false)
	if err != nil {
		return err
	}

	return nil
}
