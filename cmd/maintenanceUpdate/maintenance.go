package maintenance

import (
	"log"
	"os"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/spf13/cobra"
)

var (
	instance config.Installation
	err      error
)

func NewCmdCreate() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	maintenanceCmd := &cobra.Command{
		Use:   "maintenance",
		Short: "Use to run update and other maintenance scripts",
		Long: `Run MediaWiki maintenance operations on a Canasta installation. This command
group provides subcommands to run the standard update jobs (update.php,
runJobs.php, and SMW rebuildData.php) or execute arbitrary maintenance scripts.`,
	}

	maintenanceCmd.AddCommand(updateCmdCreate())
	maintenanceCmd.AddCommand(scriptCmdCreate())

	maintenanceCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	return maintenanceCmd
}
