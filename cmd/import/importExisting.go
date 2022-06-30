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
	)

	var err error

	createCmd := &cobra.Command{
		Use:   "import",
		Short: "Create a Canasta Installation",
		Long:  `A Command to create a Canasta Installation with Docker-compose, Kubernetes, AWS. Also allows you to import from your previous installations.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			var err error
			logging.SetVerbose(verbose)
			err = canasta.SanityChecks(databasePath, localSettingsPath)
			if err != nil {
				return err
			}
			fmt.Println("Importing Canasta")
			err = importCanasta(pwd, canastaId, domainName, path, orchestrator, databasePath, localSettingsPath, envPath)
			if err != nil {
				log.Fatal(err)
				return err
			}
			fmt.Println("Done")
			return nil
		},
	}

	pwd, err = os.Getwd()
	if err != nil {
		log.Fatal(err)
	}

	createCmd.Flags().StringVarP(&path, "path", "p", pwd, "Canasta directory")
	createCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	createCmd.Flags().StringVarP(&canastaId, "id", "i", "", "Name of the Canasta Wiki Installation")
	createCmd.Flags().StringVarP(&domainName, "domain-name", "n", "localhost", "Domain Name for the Canasta Wiki Installation")
	createCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to the existing Database dump")
	createCmd.Flags().StringVarP(&localSettingsPath, "localsettings", "l", "", "Path to the existing LocalSettings.php")
	createCmd.Flags().StringVarP(&envPath, "env", "e", "", "Path to the existing .env file")
	createCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	return createCmd
}

// importCanasta accepts all the keyword arguments and create a installation of the latest Canasta and configures it.
func importCanasta(pwd, canastaId, domainName, path, orchestrator, databasePath, localSettingsPath, envPath string) error {
	var err error
	if err = canasta.CloneStackRepo(orchestrator, &path); err != nil {
		return err
	}
	if err = canasta.CopyEnv(envPath, domainName, path, pwd); err != nil {
		return err
	}
	if err = canasta.CopyDatabase(databasePath, path, pwd); err != nil {
		return err
	}
	if err = canasta.CopyLocalSettings(localSettingsPath, path, pwd); err != nil {
		return err
	}
	if err = orchestrators.Start(path, orchestrator); err != nil {
		return err
	}
	if err = logging.Add(logging.Installation{Id: canastaId, Path: path, Orchestrator: orchestrator}); err != nil {
		return err
	}

	return err
}
