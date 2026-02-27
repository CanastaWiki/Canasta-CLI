package maintenance

import (
	"fmt"
	"sort"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/spf13/cobra"
)

func newScriptCmd(instance *config.Installation, wiki *string) *cobra.Command {

	scriptCmd := &cobra.Command{
		Use:   `script ["scriptname.php [args]"]`,
		Short: "Run maintenance scripts",
		Long: `Run a MediaWiki core maintenance script inside the web container.

With no arguments, lists all available maintenance scripts. With one argument
(a quoted script name and optional arguments), runs that script. The script
name is relative to the maintenance/ directory.

Use --wiki to target a specific wiki in a farm.`,
		Example: `  # List all available maintenance scripts
  canasta maintenance script -i myinstance

  # Run rebuildrecentchanges.php
  canasta maintenance script "rebuildrecentchanges.php" -i myinstance

  # Run a script with arguments
  canasta maintenance script "importDump.php /path/to/dump.xml" -i myinstance

  # Run a script for a specific wiki in a farm
  canasta maintenance script "rebuildrecentchanges.php" -i myinstance --wiki=docs`,
		Args: cobra.RangeArgs(0, 1),
		PreRunE: func(cmd *cobra.Command, args []string) error {
			var err error
			*instance, err = canasta.CheckCanastaId(*instance)
			return err
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 0 {
				return listMaintenanceScripts(*instance)
			}
			return runMaintenanceScript(*instance, args[0], *wiki)
		},
	}

	return scriptCmd
}

func listMaintenanceScripts(inst config.Installation) error {
	return listMaintenanceScriptsWith(nil, inst)
}

func listMaintenanceScriptsWith(orch orchestrators.Orchestrator, inst config.Installation) error {
	if orch == nil {
		var err error
		orch, err = orchestrators.New(inst.Orchestrator)
		if err != nil {
			return err
		}
	}

	cmd := `find maintenance/ -maxdepth 1 -name '*.php' -type f 2>/dev/null`
	output, _ := orch.ExecWithError(inst.Path, orchestrators.ServiceWeb, cmd)

	scripts := parseScriptNames(output)
	if len(scripts) == 0 {
		fmt.Println("No maintenance scripts found")
		return nil
	}

	sort.Strings(scripts)
	fmt.Println("Available maintenance scripts:")
	for _, script := range scripts {
		fmt.Printf("  %s\n", script)
	}
	return nil
}

func runMaintenanceScript(instance config.Installation, script string, wiki string) error {
	return runMaintenanceScriptWith(nil, instance, script, wiki)
}

func runMaintenanceScriptWith(orch orchestrators.Orchestrator, inst config.Installation, script string, wiki string) error {
	if orch == nil {
		var err error
		orch, err = orchestrators.New(inst.Orchestrator)
		if err != nil {
			return err
		}
	}

	// Reconcile --wiki from CLI flag and script string
	resolvedWiki, cleanedScript, err := resolveWikiFlag(wiki, script)
	if err != nil {
		return err
	}

	wikiFlag := ""
	if resolvedWiki != "" {
		wikiFlag = " --wiki=" + resolvedWiki
	}
	fmt.Println("Running maintenance script " + cleanedScript)
	if err := orch.ExecStreaming(inst.Path, orchestrators.ServiceWeb,
		"php maintenance/"+cleanedScript+wikiFlag); err != nil {
		return fmt.Errorf("maintenance script failed: %v", err)
	}
	fmt.Println("Completed running maintenance script")
	return nil
}
