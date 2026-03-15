package maintenance

import (
	"fmt"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newScriptCmd(instance *config.Installation) *cobra.Command {
	var wiki string

	scriptCmd := &cobra.Command{
		Use:                   "script [flags] [scriptname.php [args...]]",
		DisableFlagsInUseLine: true,
		Short:                 "Run maintenance scripts",
		Long: `Run a MediaWiki core maintenance script inside the web container.

With no arguments, lists all available maintenance scripts. With one or more
arguments, runs the specified script. The script name is relative to the
maintenance/ directory.

Flags (-i, --wiki) must come before the script name. Everything after the
script name is treated as script arguments — no quotes needed.

In a wiki farm, runs on all wikis by default. Use --wiki to target a
specific wiki.`,
		Example: `  # List all available maintenance scripts
  canasta maintenance script -i myinstance

  # Run rebuildrecentchanges.php
  canasta maintenance script -i myinstance rebuildrecentchanges.php

  # Run a script with arguments
  canasta maintenance script -i myinstance importDump.php /path/to/dump.xml

  # Run a script for a specific wiki in a farm
  canasta maintenance script -i myinstance --wiki=docs rebuildrecentchanges.php`,
		Args: cobra.ArbitraryArgs,
		PreRunE: func(_ *cobra.Command, _ []string) error {
			var err error
			*instance, err = canasta.CheckCanastaID(*instance)
			return err
		},
		RunE: func(_ *cobra.Command, args []string) error {
			if len(args) == 0 {
				return listMaintenanceScripts(*instance)
			}
			scriptStr := strings.Join(args, " ")
			return runMaintenanceScript(*instance, scriptStr, wiki)
		},
	}

	scriptCmd.Flags().StringVarP(&wiki, "wiki", "w", "", "Wiki ID to run maintenance on (default: all wikis)")
	// Stop parsing flags after the first non-flag argument (the script name).
	// This allows script arguments like -s 1000 to be passed without quotes.
	scriptCmd.Flags().SetInterspersed(false)
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

	// If a specific wiki was given, run once for that wiki.
	// Otherwise, run on all wikis in the farm.
	if resolvedWiki != "" {
		return runScriptForWiki(orch, inst, cleanedScript, resolvedWiki)
	}

	wikiIDs, err := farmsettings.GetWikiIDs(inst.Path)
	if err != nil {
		return err
	}
	for _, id := range wikiIDs {
		if err := runScriptForWiki(orch, inst, cleanedScript, id); err != nil {
			return err
		}
	}
	return nil
}

func runScriptForWiki(orch orchestrators.Orchestrator, inst config.Installation, script, wikiID string) error {
	wikiFlag := ""
	if wikiID != "" {
		wikiFlag = " --wiki=" + wikiID
	}
	fmt.Println("Running maintenance script " + script + wikiFlag)
	if err := orch.ExecStreaming(inst.Path, orchestrators.ServiceWeb,
		"php maintenance/"+script+wikiFlag); err != nil {
		return fmt.Errorf("maintenance script failed: %w", err)
	}
	fmt.Println("Completed running maintenance script")
	return nil
}
