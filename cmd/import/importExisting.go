package importExisting

import (
	"fmt"
	"log"
	"os"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/spf13/cobra"
)

func NewCmdCreate() *cobra.Command {
	var (
		workingDir               string
		path              string
		orchestrator      string
		databasePath      string
		localSettingsPath string
		envPath           string
		override          string
		canastaID         string
		domainName        string
		err               error
		keepConfig        bool
	)

	importCmd := &cobra.Command{
		Use:   "import",
		Short: "Import a wiki installation",
		Long:  `Import a wiki from your previous installation.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := canasta.SanityChecks(databasePath, localSettingsPath); err != nil {
				return err
			}
			fmt.Println("Importing Canasta")
			if err := importCanasta(workingDir, canastaID, domainName, path, orchestrator, databasePath, localSettingsPath, envPath, override); err != nil {
				fmt.Print(err.Error(), "\n")
				if !keepConfig {
					canasta.DeleteConfigAndContainers(keepConfig, path+"/"+canastaID, orchestrator)
					log.Fatal(fmt.Errorf("Import failed and files were cleaned up"))
				}
				log.Fatal(fmt.Errorf("Import failed. Keeping all the containers and config files\nExiting"))
			}
			fmt.Println("Done")
			return nil
		},
	}

	if workingDir, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}

	importCmd.Flags().StringVarP(&path, "path", "p", workingDir, "Canasta directory")
	importCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "compose", "Orchestrator to use for installation")
	importCmd.Flags().StringVarP(&canastaID, "id", "i", "", "Canasta instance ID")
	importCmd.Flags().StringVarP(&domainName, "domain-name", "n", "localhost", "Domain name")
	importCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to the existing database dump")
	importCmd.Flags().StringVarP(&localSettingsPath, "localsettings", "l", "", "Path to the existing LocalSettings.php")
	importCmd.Flags().StringVarP(&envPath, "env", "e", "", "Path to the existing .env file")
	importCmd.Flags().StringVarP(&override, "override", "r", "", "Name of a file to copy to docker-compose.override.yml")
	importCmd.Flags().BoolVarP(&keepConfig, "keep-config", "k", false, "Keep the config files on installation failure")

	return importCmd
}

// importCanasta copies LocalSettings.php and databasedump to create canasta from a previous mediawiki installation
func importCanasta(workingDir, canastaID, domainName, path, orchestrator, databasePath, localSettingsPath, envPath, override string) error {
	if _, err := config.GetDetails(canastaID); err == nil {
		log.Fatal(fmt.Errorf("Canasta installation with the ID already exist!"))
	}
	if err := canasta.CloneStackRepo(orchestrator, canastaID, &path); err != nil {
		return err
	}
	if err := canasta.CopyEnvFile(envPath, path, workingDir); err != nil {
		return err
	}
	if err := canasta.CopyDatabase(databasePath, path, workingDir); err != nil {
		return err
	}
	if err := canasta.CopyLocalSettings(localSettingsPath, path, workingDir); err != nil {
		return err
	}
	if err := orchestrators.CopyOverrideFile(path, orchestrator, override, workingDir); err != nil {
		return err
	}
	instance := config.Installation{Id: canastaID, Path: path, Orchestrator: orchestrator}
	if err := orchestrators.Start(instance); err != nil {
		return err
	}
	if err := config.Add(instance); err != nil {
		return err
	}
	return nil
}
