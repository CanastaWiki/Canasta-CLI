package importExisting

import (
	"fmt"
	"log"
	"os"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func NewCmdCreate() *cobra.Command {
	var (
		pwd               string
		path              string
		orchestrator      string
		databasePath      string
		localSettingsPath string
		envPath           string
		canastaId         string
		domainName        string
		verbose           bool
		err               error
	)

	createCmd := &cobra.Command{
		Use:   "import",
		Short: "Import a wiki installation",
		Long:  `Import a wiki from your previous installation.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			if err := canasta.SanityChecks(databasePath, localSettingsPath); err != nil {
				return err
			}
			fmt.Println("Importing Canasta")
			if err := importCanasta(pwd, canastaId, domainName, path, orchestrator, databasePath, localSettingsPath, envPath); err != nil {
				log.Fatal(err)
			}
			fmt.Println("Done")
			return nil
		},
	}

	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}

	createCmd.Flags().StringVarP(&path, "path", "p", pwd, "Canasta directory")
	createCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	createCmd.Flags().StringVarP(&canastaId, "id", "i", "", "Canasta instance ID")
	createCmd.Flags().StringVarP(&domainName, "domain-name", "n", "localhost", "Domain name")
	createCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to the existing database dump")
	createCmd.Flags().StringVarP(&localSettingsPath, "localsettings", "l", "", "Path to the existing LocalSettings.php")
	createCmd.Flags().StringVarP(&envPath, "env", "e", "", "Path to the existing .env file")
	createCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose output")
	return createCmd
}

// importCanasta copies LocalSettings.php and databasedump to create canasta from a previous mediawiki installation
func importCanasta(pwd, canastaId, domainName, path, orchestrator, databasePath, localSettingsPath, envPath string) error {
	canasta.CloneStackRepo(orchestrator, canastaId, &path)
	canasta.CopyEnv(envPath, domainName, path, pwd)
	if err := canasta.CopyDatabase(databasePath, path, pwd); err != nil {
		return err
	}
	if err := canasta.CopyLocalSettings(localSettingsPath, path, pwd); err != nil {
		return err
	}
	if err := orchestrators.Start(path, orchestrator); err != nil {
		return err
	}
	if err := logging.Add(logging.Installation{Id: canastaId, Path: path, Orchestrator: orchestrator}); err != nil {
		return err
	}
	return nil
}
