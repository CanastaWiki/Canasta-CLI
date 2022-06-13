package create

import (
	"fmt"
	"log"
	"os/exec"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/git"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func NewCmdCreate() *cobra.Command {
	var (
		Path              string
		Orchestrator      string
		DatabasePath      string
		LocalSettingsPath string
		EnvPath           string
	)

	createCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Canasta Installation",
		Long:  `A Command to create a Canasta Installation with Docker-compose, Kubernetes, AWS. Also allows you to import from your previous installations.`,
		Run: func(cmd *cobra.Command, args []string) {
			err := createCanasta(Path, Orchestrator, DatabasePath, LocalSettingsPath, EnvPath)
			if err != nil {
				log.Fatal(err)
			}
		},
	}

	createCmd.Flags().StringVarP(&Path, "path", "p", "", "Canasta Installation directory")
	createCmd.Flags().StringVarP(&Orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	createCmd.Flags().StringVarP(&DatabasePath, "database", "d", "", "Path to the existing database dump")
	createCmd.Flags().StringVarP(&LocalSettingsPath, "localsettings", "l", "", "Path to the existing LocalSettings.php")
	createCmd.Flags().StringVarP(&EnvPath, "env", "e", "", "Path to the existing .env file")
	createCmd.MarkFlagRequired("path")

	return createCmd
}

// createCanasta accepts all the keyword arguments and create a installation of the latest Canasta and configures it.
func createCanasta(Path, Orchestrator, DatabasePath, LocalSettingsPath, EnvPath string) error {

	fmt.Printf("Cloning the %s stack repo \n", Orchestrator)

	Path += "/Canasta-" + Orchestrator + "/"
	err := cloneStackRepo(Orchestrator, Path)
	if err != nil {
		return err
	}

	fmt.Printf("Copying .env.example to .env\n")
	err = exec.Command("cp", Path+"/.env.example", Path+"/.env").Run()
	if err != nil {
		return err
	}

	fmt.Printf("Starting the containers\n")
	err = orchestrators.Up(Path, Orchestrator)
	if err != nil {
		return err
	}

	fmt.Printf("Configuring Mediawiki Installation\n")
	err = mediawiki.Install(Path, Orchestrator, DatabasePath, LocalSettingsPath, EnvPath)
	if err != nil {
		return err
	}

	fmt.Printf("Restarting the containers\n")
	err = orchestrators.DownUp(Path, Orchestrator)
	if err != nil {
		return err
	}

	fmt.Printf("\nCanasta have been succesffuly installed and configured.Below are the details:\nPath: %s,\nOrchestrator: %s\nUsername: %s\nPassword: %s\n", Path, Orchestrator, "root", "password")

	return nil

}

// cloneStackRepo accept the orchestrator from the cli and pass the corresponding reopository link
// and clones the repo to a new folder in the specified path
func cloneStackRepo(orchestrator, path string) error {

	repo, err := orchestrators.GetRepoLink(orchestrator)
	if err != nil {
		return err
	}

	err = git.Clone(repo, path)
	if err != nil {
		return err
	}

	return nil
}
