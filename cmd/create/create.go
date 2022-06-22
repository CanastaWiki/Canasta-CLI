package create

import (
	"fmt"
	"log"
	"os"
	"os/exec"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/git"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func NewCmdCreate() *cobra.Command {
	var (
		path              string
		orchestrator      string
		wikiName          string
		adminName         string
		adminPassword     string
		databasePath      string
		localSettingsPath string
		envPath           string
		userVariables     map[string]string
		wikiId            string
	)

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
			userVariables, err = mediawiki.PromptUser(userVariables)
			if err != nil {
				log.Fatal("Canasta: ", err)
			}

			err = createCanasta(path, orchestrator, databasePath, localSettingsPath, envPath, userVariables)
			if err != nil {
				log.Fatal("Canasta: ", err)
			}
		},
	}

	// Defaults the path's value to the current working directory if no value is passed
	pwd, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}

	createCmd.Flags().StringVarP(&path, "path", "p", pwd, "Canasta directory")
	createCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	createCmd.Flags().StringVarP(&wikiName, "wiki", "w", "", "Name of the Wiki Installation")
	createCmd.Flags().StringVarP(&wikiId, "id", "i", "", "Canasta ID to differentiate between different Canasta Installations")
	createCmd.Flags().StringVarP(&adminName, "admin", "a", "", "Name of the Admin user")
	createCmd.Flags().StringVarP(&adminPassword, "password", "s", "", "Password for the Admin user")
	createCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to the existing Database dump")
	createCmd.Flags().StringVarP(&localSettingsPath, "localsettings", "l", "", "Path to the existing LocalSettings.php")
	createCmd.Flags().StringVarP(&envPath, "env", "e", "", "Path to the existing .env file")
	return createCmd
}

// createCanasta accepts all the keyword arguments and create a installation of the latest Canasta and configures it.
func createCanasta(wikiId, path, orchestrator, databasePath, localSettingsPath, envPath string, userVariables map[string]string) error {
	var err error

	fmt.Printf("Cloning the %s stack repo to %s \n", orchestrator, path)
	err = cloneStackRepo(orchestrator, &path)
	if err != nil {
		return err
	}

	fmt.Printf("Copying .env.example to .env\n")
	err = exec.Command("cp", path+"/.env.example", path+"/.env").Run()
	if err != nil {
		return err
	}

	fmt.Printf("Starting the containers\n")
	err = orchestrators.Start(path, orchestrator)
	if err != nil {
		return err
	}

	fmt.Printf("Configuring Mediawiki Installation\n")
	_, err = mediawiki.Install(path, orchestrator, databasePath, localSettingsPath, envPath, userVariables)
	if err != nil {
		return err
	}

	err = logging.Add(logging.Installation{Id: wikiId, Path: path, Orchestrator: orchestrator})
	if err != nil {
		return err
	}

	fmt.Printf("Restarting the containers\n")
	err = orchestrators.StopAndStart(path, orchestrator)
	if err != nil {
		return err
	}

	return err

}

// cloneStackRepo accept the orchestrator from the cli and pass the corresponding reopository link
// and clones the repo to a new folder in the specified path
func cloneStackRepo(orchestrator string, path *string) error {
	*path += "/canasta-" + orchestrator
	repo, err := orchestrators.GetRepoLink(orchestrator)
	if err != nil {
		return err
	}

	err = git.Clone(repo, *path)
	if err != nil {
		return err
	}

	return nil
}
