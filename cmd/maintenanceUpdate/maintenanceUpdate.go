package maintenance

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var (
	skipJobs bool
	skipSMW  bool
)

func newUpdateCmd() *cobra.Command {

	updateCmd := &cobra.Command{
		Use:   "update",
		Short: "Run maintenance update jobs",
		Long: `Run the standard MediaWiki maintenance update sequence: update.php,
runJobs.php, and Semantic MediaWiki's rebuildData.php. This is typically
needed after upgrading MediaWiki or enabling new extensions.

By default, all three scripts are run. Use --skip-jobs to skip runJobs.php
and --skip-smw to skip rebuildData.php.

In a wiki farm, use --wiki to target a specific wiki, or --all to run
maintenance on every wiki. If there is only one wiki, it is selected
automatically.`,
		Example: `  canasta maintenance update -i myinstance
  canasta maintenance update -i myinstance --wiki=docs
  canasta maintenance update -i myinstance --all
  canasta maintenance update -i myinstance --skip-jobs --skip-smw`,
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			if wiki != "" && all {
				return fmt.Errorf("cannot use --wiki with --all")
			}
			if all {
				wikiIDs, err := getWikiIDs(instance)
				if err != nil {
					return err
				}
				for _, id := range wikiIDs {
					if err := runMaintenanceUpdate(instance, id); err != nil {
						return err
					}
				}
			} else if wiki != "" {
				if err := runMaintenanceUpdate(instance, wiki); err != nil {
					return err
				}
			} else {
				wikiIDs, err := getWikiIDs(instance)
				if err != nil {
					return err
				}
				if len(wikiIDs) == 1 {
					if err := runMaintenanceUpdate(instance, wikiIDs[0]); err != nil {
						return err
					}
				} else {
					return fmt.Errorf("multiple wikis found; use --wiki=<id> or --all")
				}
			}
			return nil
		},
	}

	updateCmd.Flags().BoolVar(&skipJobs, "skip-jobs", false, "Skip running runJobs.php")
	updateCmd.Flags().BoolVar(&skipSMW, "skip-smw", false, "Skip running Semantic MediaWiki rebuildData.php")

	return updateCmd
}

func getWikiIDs(instance config.Installation) ([]string, error) {
	yamlPath := filepath.Join(instance.Path, "config", "wikis.yaml")
	ids, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read wikis.yaml: %v", err)
	}
	return ids, nil
}

func runMaintenanceUpdate(instance config.Installation, wikiID string) error {
	orch, err := orchestrators.New(instance.Orchestrator)
	if err != nil {
		return err
	}

	wikiFlag := ""
	if wikiID != "" {
		wikiFlag = " --wiki=" + wikiID
	}
	wikiMsg := ""
	if wikiID != "" {
		wikiMsg = " for wiki '" + wikiID + "'"
	}

	fmt.Printf("Running update.php%s...\n", wikiMsg)
	if err := orch.ExecStreaming(instance.Path, "web",
		"php maintenance/update.php --quick"+wikiFlag); err != nil {
		return fmt.Errorf("update.php failed%s: %v", wikiMsg, err)
	}

	if !skipJobs {
		fmt.Printf("Running runJobs.php%s...\n", wikiMsg)
		if err := orch.ExecStreaming(instance.Path, "web",
			"php maintenance/runJobs.php"+wikiFlag); err != nil {
			return fmt.Errorf("runJobs.php failed%s: %v", wikiMsg, err)
		}
	}

	if !skipSMW {
		const rebuildScript = "extensions/SemanticMediaWiki/maintenance/rebuildData.php"
		checkCmd := fmt.Sprintf("test -f %s && echo exists", rebuildScript)
		checkOutput, _ := orch.ExecWithError(instance.Path, "web", checkCmd)
		if !strings.Contains(checkOutput, "exists") {
			fmt.Printf("Semantic MediaWiki not installed%s, skipping rebuildData.php\n", wikiMsg)
		} else {
			fmt.Printf("Running rebuildData.php%s...\n", wikiMsg)
			if err := orch.ExecStreaming(instance.Path, "web",
				"php "+rebuildScript+wikiFlag); err != nil {
				fmt.Printf("rebuildData.php failed%s: %v\n", wikiMsg, err)
			}
		}
	}

	fmt.Printf("Completed maintenance%s\n", wikiMsg)
	return nil
}
