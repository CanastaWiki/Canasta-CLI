package maintenanceupdate

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

var (
	instance config.Installation
	pwd      string
	err      error
)

func NewCmdCreate() *cobra.Command {
	maintenanceCmd := &cobra.Command{
		Use:   "maintenance",
		Short: "Run maintenance update jobs",
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		Run: func(cmd *cobra.Command, args []string) {
			runMaintenanceUpdate(instance)
		},
	}

	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}
	maintenanceCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	maintenanceCmd.PersistentFlags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	return maintenanceCmd
}

func runMaintenanceUpdate(instance config.Installation) {
	fmt.Println("Running maintenance jobs")
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "php maintenance/update.php && php maintenance/runJobs.php && php canasta-extensions/SemanticMediaWiki/maintenance/rebuildData.php")
	fmt.Println("Completed running maintenance jobs")

}
