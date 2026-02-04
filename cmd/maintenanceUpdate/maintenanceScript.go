package maintenance

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/spf13/cobra"
)

func scriptCmdCreate() *cobra.Command {

	scriptCmd := &cobra.Command{
		Use:   `script "[scriptname.php] [args]"`,
		Short: "Run maintenance scripts",
		Long: `Run an arbitrary MediaWiki maintenance script inside the web container.
The script name is relative to the maintenance/ directory. Any additional
arguments are passed directly to the PHP script.`,
		Example: `  # Run rebuildrecentchanges.php
  canasta maintenance script "rebuildrecentchanges.php" -i myinstance

  # Run a script with arguments
  canasta maintenance script "importDump.php /path/to/dump.xml" -i myinstance`,
		Args:  cobra.ExactArgs(1),
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		Run: func(cmd *cobra.Command, args []string) {
			logging.SetVerbose(true)
			runMaintenanceScript(instance, args[0])
		},
	}

	return scriptCmd
}

func runMaintenanceScript(instance config.Installation, script string) {
	fmt.Println("Running maintenance script " + script)
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "php maintenance/"+script)
	fmt.Println("Completed running maintenance script")

}
