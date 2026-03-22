package maintenance

import (
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

func NewCmd() *cobra.Command {
	var instance config.Instance

	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		logging.Fatal(wdErr)
	}
	instance.Path = workingDir

	maintenanceCmd := &cobra.Command{
		Use:   "maintenance",
		Short: "Use to run update and other maintenance scripts",
		Long: `Run MediaWiki maintenance operations on a Canasta instance. This command
group provides subcommands to run the standard update sequence, execute
arbitrary core maintenance scripts, or run extension-specific maintenance scripts.`,
	}

	maintenanceCmd.AddCommand(newUpdateCmd(&instance))
	maintenanceCmd.AddCommand(newScriptCmd(&instance))
	maintenanceCmd.AddCommand(newExtensionCmd(&instance))
	maintenanceCmd.AddCommand(newExecCmd(&instance))

	maintenanceCmd.PersistentFlags().StringVarP(&instance.ID, "id", "i", "", "Canasta instance ID (defaults to instance associated with current directory)")
	return maintenanceCmd
}
