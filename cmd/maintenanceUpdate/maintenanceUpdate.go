package maintenance

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func updateCmdCreate() *cobra.Command {

	updateCmd := &cobra.Command{
		Use:   "update",
		Short: "Run maintenance update jobs",
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		Run: func(cmd *cobra.Command, args []string) {
			runMaintenanceUpdate(instance)
		},
	}

	return updateCmd
}

func runMaintenanceUpdate(instance config.Installation) {
	fmt.Println("Running maintenance jobs")
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "php maintenance/update.php && php maintenance/runJobs.php && php canasta-extensions/SemanticMediaWiki/maintenance/rebuildData.php")
	fmt.Println("Completed running maintenance jobs")

}
