package maintenance

import (
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/spf13/cobra"
)

var (
	instance config.Installation
	err      error
)

func NewCmdCreate() *cobra.Command {
	maintenanceCmd := &cobra.Command{
		Use:   "maintenance",
		Short: "Use to run update and other maintenance scripts",
	}

	maintenanceCmd.AddCommand(updateCmdCreate())
	maintenanceCmd.AddCommand(scriptCmdCreate())

	maintenanceCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	return maintenanceCmd
}
