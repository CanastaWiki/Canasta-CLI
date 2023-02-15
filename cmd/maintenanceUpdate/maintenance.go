package maintenance

import (
	"log"
	"os"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/spf13/cobra"
)

var (
	instance config.Installation
	pwd      string
	err      error
)

func NewCmdCreate() *cobra.Command {
	maintenanceCmd := &cobra.Command{
		Use:   "maintenance",
		Short: "Use to run update and other maintenance scripts",
	}

	maintenanceCmd.AddCommand(updateCmdCreate())
	maintenanceCmd.AddCommand(scriptCmdCreate())
	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}

	maintenanceCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	maintenanceCmd.PersistentFlags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	return maintenanceCmd
}
