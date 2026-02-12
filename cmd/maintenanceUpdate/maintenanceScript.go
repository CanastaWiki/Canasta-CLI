package maintenance

import (
	"fmt"
	"log"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/spf13/cobra"
)

func scriptCmdCreate() *cobra.Command {

	scriptCmd := &cobra.Command{
		Use:   `script "[scriptname.php] [args]"`,
		Short: "Run maintenance scripts",
		Long: `Run an arbitrary MediaWiki maintenance script inside the web container.
The script name is relative to the maintenance/ directory. Any additional
arguments are passed directly to the PHP script.

Use --wiki to target a specific wiki in a farm.`,
		Example: `  # Run rebuildrecentchanges.php
  canasta maintenance script "rebuildrecentchanges.php" -i myinstance

  # Run a script with arguments
  canasta maintenance script "importDump.php /path/to/dump.xml" -i myinstance

  # Run a script for a specific wiki in a farm
  canasta maintenance script "rebuildrecentchanges.php" -i myinstance --wiki=docs`,
		Args: cobra.ExactArgs(1),
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		Run: func(cmd *cobra.Command, args []string) {
			runMaintenanceScript(instance, args[0], wiki)
		},
	}

	return scriptCmd
}

func runMaintenanceScript(instance config.Installation, script string, wiki string) {
	orch := orchestrators.New(instance.Orchestrator)

	wikiFlag := ""
	if wiki != "" {
		wikiFlag = " --wiki=" + wiki
	}
	fmt.Println("Running maintenance script " + script)
	if err := orch.ExecStreaming(instance.Path, "web",
		"php maintenance/"+script+wikiFlag); err != nil {
		log.Fatalf("maintenance script failed: %v", err)
	}
	fmt.Println("Completed running maintenance script")
}
