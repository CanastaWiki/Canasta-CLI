package maintenance

import (
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func smwRebuildCmdCreate() *cobra.Command {

	smwRebuildCmd := &cobra.Command{
		Use:   "smw-rebuild [-- [rebuildData.php options]]",
		Short: "Run Semantic MediaWiki rebuildData.php",
		Long: `Run Semantic MediaWiki's rebuildData.php maintenance script to populate
or rebuild the SMW store. Any arguments after -- are passed directly to
rebuildData.php (e.g., --startidfile, -s, -e, --skip-properties).

In a wiki farm, use --wiki to target a specific wiki, or --all to run
the rebuild on every wiki. If there is only one wiki, it is selected
automatically.`,
		Example: `  # Basic rebuild
  canasta maintenance smw-rebuild -i myinstance

  # Rebuild specific wiki in a farm
  canasta maintenance smw-rebuild -i myinstance --wiki=docs

  # Rebuild all wikis
  canasta maintenance smw-rebuild -i myinstance --all

  # Pass options to rebuildData.php
  canasta maintenance smw-rebuild -i myinstance -- --startidfile /tmp/progress

  # Rebuild a specific page range
  canasta maintenance smw-rebuild -i myinstance -- -s 1000 -e 2000`,
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			if wiki != "" && all {
				return fmt.Errorf("cannot use --wiki with --all")
			}

			extraArgs := strings.Join(args, " ")

			if all {
				wikiIDs, err := getWikiIDs(instance)
				if err != nil {
					return err
				}
				for _, id := range wikiIDs {
					if err := runSMWRebuild(instance, id, extraArgs); err != nil {
						return err
					}
				}
			} else if wiki != "" {
				if err := runSMWRebuild(instance, wiki, extraArgs); err != nil {
					return err
				}
			} else {
				wikiIDs, err := getWikiIDs(instance)
				if err != nil {
					return err
				}
				if len(wikiIDs) == 1 {
					if err := runSMWRebuild(instance, wikiIDs[0], extraArgs); err != nil {
						return err
					}
				} else {
					return fmt.Errorf("multiple wikis found; use --wiki=<id> or --all")
				}
			}
			return nil
		},
	}

	return smwRebuildCmd
}

func runSMWRebuild(instance config.Installation, wikiID string, extraArgs string) error {
	return runSMWRebuildWith(nil, instance, wikiID, extraArgs)
}

func runSMWRebuildWith(orch orchestrators.Orchestrator, instance config.Installation, wikiID string, extraArgs string) error {
	if orch == nil {
		var err error
		orch, err = orchestrators.New(instance.Orchestrator)
		if err != nil {
			return err
		}
	}

	const rebuildScript = "extensions/SemanticMediaWiki/maintenance/rebuildData.php"

	// Check that SMW is installed
	checkCmd := fmt.Sprintf("test -f %s && echo exists", rebuildScript)
	checkOutput, _ := orch.ExecWithError(instance.Path, "web", checkCmd)
	if !strings.Contains(checkOutput, "exists") {
		return fmt.Errorf("Semantic MediaWiki is not installed")
	}

	wikiFlag := ""
	wikiMsg := ""
	if wikiID != "" {
		wikiFlag = " --wiki=" + wikiID
		wikiMsg = " for wiki '" + wikiID + "'"
	}

	cmd := "php " + rebuildScript + wikiFlag
	if extraArgs != "" {
		cmd += " " + extraArgs
	}

	fmt.Printf("Running rebuildData.php%s...\n", wikiMsg)
	if err := orch.ExecStreaming(instance.Path, "web", cmd); err != nil {
		return fmt.Errorf("rebuildData.php failed%s: %v", wikiMsg, err)
	}

	fmt.Printf("Completed rebuildData.php%s\n", wikiMsg)
	return nil
}
