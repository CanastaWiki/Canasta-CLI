package extension

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmd() *cobra.Command {
	var (
		instance config.Installation
		orch     orchestrators.Orchestrator
		wiki     string
	)
	constants := extensionsskins.Item{Name: "Canasta extension", RelativeInstallationPath: "extensions", PhpCommand: "wfLoadExtension"}

	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	extensionCmd := &cobra.Command{
		Use:   "extension",
		Short: "Manage Canasta extensions",
		Long: `Manage MediaWiki extensions in a Canasta installation. Subcommands allow you
to list all available extensions, and enable or disable them globally or for
a specific wiki in a farm.`,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			var err error
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			orch, err = orchestrators.New(instance.Orchestrator)
			return err
		},
	}

	extensionCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	extensionCmd.PersistentFlags().StringVarP(&wiki, "wiki", "w", "", "ID of the specific wiki within the Canasta farm")

	extensionCmd.AddCommand(newListCmd(&instance, &orch, &wiki, &constants))
	extensionCmd.AddCommand(newEnableCmd(&instance, &orch, &wiki, &constants))
	extensionCmd.AddCommand(newDisableCmd(&instance, &orch, &wiki, &constants))

	return extensionCmd
}
