package extension

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var (
	instance     config.Installation
	orch         orchestrators.Orchestrator
	wiki         string
	err          error
	verbose      bool
	extensionCmd *cobra.Command
	constants    = extensionsskins.Item{Name: "Canasta extension", RelativeInstallationPath: "canasta-extensions", PhpCommand: "wfLoadExtension"}
)

func NewCmdCreate() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	extensionCmd = &cobra.Command{
		Use:   "extension",
		Short: "Manage Canasta extensions",
		Long: `Manage MediaWiki extensions in a Canasta installation. Subcommands allow you
to list all available extensions, and enable or disable them globally or for
a specific wiki in a farm.`,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			orch = orchestrators.New(instance.Orchestrator)
			return nil
		},
	}

	extensionCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	extensionCmd.PersistentFlags().StringVarP(&wiki, "wiki", "w", "", "ID of the specific wiki within the Canasta farm")
	extensionCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")

	extensionCmd.AddCommand(listCmdCreate())
	extensionCmd.AddCommand(enableCmdCreate())
	extensionCmd.AddCommand(disableCmdCreate())

	return extensionCmd
}
