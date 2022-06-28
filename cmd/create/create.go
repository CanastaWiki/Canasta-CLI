package create

import (
	"log"
	"os"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func NewCmdCreate() *cobra.Command {
	var (
		pwd               string
		path              string
		orchestrator      string
		wikiName          string
		adminName         string
		adminPassword     string
		databasePath      string
		localSettingsPath string
		envPath           string
		canastaId         string
		userVariables     map[string]string
	)

	var err error

	createCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Canasta Installation",
		Long:  `A Command to create a Canasta Installation with Docker-compose, Kubernetes, AWS. Also allows you to import from your previous installations.`,
		Run: func(cmd *cobra.Command, args []string) {
			var err error
			userVariables = map[string]string{
				"wikiName":      wikiName,
				"adminName":     adminName,
				"adminPassword": adminPassword,
				"dbUser":        "root",
			}
			log.SetFlags(0)
			canastaId, userVariables, err = mediawiki.PromptUser(canastaId, userVariables)
			if err != nil {
				log.Fatal("Canasta: ", err)
			}
			// err = databaseSanityChecks(databasePath)
			// if err != nil {
			// 	log.Fatal("Database Path:", err)
			// }

			err = createCanasta(pwd, canastaId, path, orchestrator, databasePath, localSettingsPath, envPath, userVariables)
			if err != nil {
				log.Fatal("Canasta: ", err)
			}
		},
	}

	// Defaults the path's value to the current working directory if no value is passed
	pwd, err = os.Getwd()
	if err != nil {
		log.Fatal(err)
	}

	createCmd.Flags().StringVarP(&path, "path", "p", pwd, "Canasta directory")
	createCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	createCmd.Flags().StringVarP(&wikiName, "wiki", "w", "", "Name of the Canasta Wiki Installation")
	createCmd.Flags().StringVarP(&canastaId, "id", "i", "", "Name of the Canasta Wiki Installation")
	createCmd.Flags().StringVarP(&adminName, "admin", "a", "", "Name of the Admin user")
	createCmd.Flags().StringVarP(&adminPassword, "password", "s", "", "Password for the Admin user")
	createCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to the existing Database dump")
	createCmd.Flags().StringVarP(&localSettingsPath, "localsettings", "l", "", "Path to the existing LocalSettings.php")
	createCmd.Flags().StringVarP(&envPath, "env", "e", "", "Path to the existing .env file")
	return createCmd
}

// createCanasta accepts all the keyword arguments and create a installation of the latest Canasta and configures it.
func createCanasta(pwd, canastaId, path, orchestrator, databasePath, localSettingsPath, envPath string, userVariables map[string]string) error {
	var err error
	if err = canasta.CloneStackRepo(orchestrator, &path); err != nil {
		return err
	}
	if err = canasta.CopyEnv(envPath, path, pwd); err != nil {
		return err
	}
	if err = orchestrators.Start(path, orchestrator); err != nil {
		return err
	}
	if _, err = mediawiki.Install(path, orchestrator, databasePath, localSettingsPath, envPath, userVariables); err != nil {
		return err
	}
	if err = logging.Add(logging.Installation{Id: canastaId, Path: path, Orchestrator: orchestrator}); err != nil {
		return err
	}
	if err = orchestrators.StopAndStart(path, orchestrator); err != nil {
		return err
	}

	return err

}
