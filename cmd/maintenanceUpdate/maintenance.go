package maintenance

import (
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

var (
	instance config.Installation
	wiki     string
	all      bool
	err      error
)

func NewCmd() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		logging.Fatal(wdErr)
	}
	instance.Path = workingDir

	maintenanceCmd := &cobra.Command{
		Use:   "maintenance",
		Short: "Use to run update and other maintenance scripts",
		Long: `Run MediaWiki maintenance operations on a Canasta installation. This command
group provides subcommands to run the standard update sequence, execute
arbitrary core maintenance scripts, or run extension-specific maintenance scripts.`,
	}

	maintenanceCmd.AddCommand(newUpdateCmd())
	maintenanceCmd.AddCommand(newScriptCmd())
	maintenanceCmd.AddCommand(newExtensionCmd())
	maintenanceCmd.AddCommand(newExecCmd())

	maintenanceCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	maintenanceCmd.PersistentFlags().StringVarP(&wiki, "wiki", "w", "", "Wiki ID to run maintenance on")
	maintenanceCmd.PersistentFlags().BoolVar(&all, "all", false, "Run for all wikis in the farm")
	return maintenanceCmd
}
