package create

import (
	"bufio"
	"fmt"
	"log"
	"os"
	"os/exec"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var (
		path         string
		orchestrator string
		pwd          string
		err          error
		keepConfig   bool
		canastaInfo  canasta.CanastaVariables
	)
	createCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Canasta installation",
		Long:  "Creates a Canasta installation using an orchestrator of your choice.",
		PreRun: func(cmd *cobra.Command, args []string) {
			_, err := exec.LookPath("docker-compose")
			if err != nil {
				logging.Fatal(fmt.Errorf("docker-compose should be installed! (%s)", err))
			}
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			if canastaInfo, err = mediawiki.PromptUser(canastaInfo); err != nil {
				logging.Fatal(err)
			}
			fmt.Println("Setting up Canasta")
			if err = createCanasta(canastaInfo, pwd, path, orchestrator); err != nil {
				fmt.Print(err.Error(), "\n")
				if keepConfig {
					logging.Fatal(fmt.Errorf("Keeping all the containers and config files\nExiting"))
				}
				scanner := bufio.NewScanner(os.Stdin)
				fmt.Println("A fatal error occured during the installation\nDo you want to keep the files related to it? (y/n)")
				scanner.Scan()
				input := scanner.Text()
				if input == "y" || input == "Y" || input == "yes" {
					logging.Fatal(fmt.Errorf("Keeping all the containers and config files\nExiting"))
				}
				canasta.DeleteConfigAndContainers(keepConfig, path+"/"+canastaInfo.Id, orchestrator)
			}
			fmt.Println("Done")
			return nil
		},
	}

	if pwd, err = os.Getwd(); err != nil {
		logging.Fatal(err)
	}

	createCmd.Flags().StringVarP(&path, "path", "p", pwd, "Canasta directory")
	createCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	createCmd.Flags().StringVarP(&canastaInfo.Id, "id", "i", "", "Canasta instance ID")
	createCmd.Flags().StringVarP(&canastaInfo.WikiName, "wiki", "w", "", "Name of wiki")
	createCmd.Flags().StringVarP(&canastaInfo.DomainName, "domain-name", "n", "localhost", "Domain name")
	createCmd.Flags().StringVarP(&canastaInfo.AdminName, "admin", "a", "", "Initial wiki admin username")
	createCmd.Flags().StringVarP(&canastaInfo.AdminPassword, "password", "s", "", "Initial wiki admin password")
	createCmd.Flags().BoolVarP(&keepConfig, "keep-config", "k", false, "Keep the config files on installation failure")
	return createCmd
}

// importCanasta accepts all the keyword arguments and create a installation of the latest Canasta.
func createCanasta(canastaInfo canasta.CanastaVariables, pwd, path, orchestrator string) error {
	if _, err := logging.GetDetails(canastaInfo.Id); err == nil {
		log.Fatal(fmt.Errorf("Canasta installation with the ID already exist!"))
	}
	if err := canasta.CloneStackRepo(orchestrator, canastaInfo.Id, &path); err != nil {
		return err
	}
	if err := canasta.CopyEnv("", canastaInfo.DomainName, path, pwd); err != nil {
		return err
	}
	if err := orchestrators.Start(path, orchestrator); err != nil {
		return err
	}
	if _, err := mediawiki.Install(path, orchestrator, canastaInfo); err != nil {
		return err
	}
	if err := logging.Add(logging.Installation{Id: canastaInfo.Id, Path: path, Orchestrator: orchestrator}); err != nil {
		return err
	}
	if err := orchestrators.StopAndStart(path, orchestrator); err != nil {
		log.Fatal(err)
	}
	return nil
}
