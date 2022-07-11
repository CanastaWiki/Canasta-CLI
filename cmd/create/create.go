package create

import (
	"fmt"
	"os"

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
		verbose      bool
		err          error
		canastaInfo  canasta.CanastaVariables
	)
	createCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Canasta Installation",
		Long:  `A Command to create a Canasta Installation with Docker-compose, Kubernetes, AWS. Also allows you to import from your previous installations.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			if canastaInfo, err = mediawiki.PromptUser(canastaInfo); err != nil {
				logging.Fatal(err)
			}
			fmt.Println("Setting up Canasta")
			if err = createCanasta(canastaInfo, pwd, path, orchestrator); err != nil {
				orchestrators.Delete(path, orchestrator)
				logging.Fatal(err)
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
	createCmd.Flags().StringVarP(&canastaInfo.Id, "id", "i", "", "Name of the Canasta Wiki Installation")
	createCmd.Flags().StringVarP(&canastaInfo.WikiName, "wiki", "w", "", "Name of the Canasta Wiki Installation")
	createCmd.Flags().StringVarP(&canastaInfo.DomainName, "domain-name", "n", "localhost", "Domain Name for the Canasta Wiki Installation")
	createCmd.Flags().StringVarP(&canastaInfo.AdminName, "admin", "a", "", "Name of the Admin user")
	createCmd.Flags().StringVarP(&canastaInfo.AdminPassword, "password", "s", "", "Password for the Admin user")
	createCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	return createCmd
}

// importCanasta accepts all the keyword arguments and create a installation of the latest Canasta.
func createCanasta(canastaInfo canasta.CanastaVariables, pwd, path, orchestrator string) error {
	canasta.CloneStackRepo(orchestrator, canastaInfo.Id, &path)
	canasta.CopyEnv("", canastaInfo.DomainName, path, pwd)
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
		return err
	}
	return nil
}
