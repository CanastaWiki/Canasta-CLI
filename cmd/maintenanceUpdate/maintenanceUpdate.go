package maintenance

import (
	"fmt"
	"log"
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

func updateCmdCreate() *cobra.Command {

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
		Run: func(cmd *cobra.Command, args []string) {
			if wiki != "" && all {
				log.Fatal("cannot use --wiki with --all")
			}
			if all {
				wikiIDs := getWikiIDs(instance)
				for _, id := range wikiIDs {
					runMaintenanceUpdate(instance, id)
				}
			} else if wiki != "" {
				runMaintenanceUpdate(instance, wiki)
			} else {
				wikiIDs := getWikiIDs(instance)
				if len(wikiIDs) == 1 {
					runMaintenanceUpdate(instance, wikiIDs[0])
				} else {
					log.Fatal("multiple wikis found; use --wiki=<id> or --all")
				}
			}
		},
	}

	updateCmd.Flags().BoolVar(&skipJobs, "skip-jobs", false, "Skip running runJobs.php")
	updateCmd.Flags().BoolVar(&skipSMW, "skip-smw", false, "Skip running Semantic MediaWiki rebuildData.php")

	return updateCmd
}

func getWikiIDs(instance config.Installation) []string {
	yamlPath := filepath.Join(instance.Path, "config", "wikis.yaml")
	ids, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		log.Fatalf("failed to read wikis.yaml: %v", err)
	}
	return ids
}

func runMaintenanceUpdate(instance config.Installation, wikiID string) {
	wikiFlag := ""
	if wikiID != "" {
		wikiFlag = " --wiki=" + wikiID
	}
	wikiMsg := ""
	if wikiID != "" {
		wikiMsg = " for wiki '" + wikiID + "'"
	}

	fmt.Printf("Running update.php%s...\n", wikiMsg)
	if err := orchestrators.ExecStreaming(instance.Path, instance.Orchestrator, "web",
		"php maintenance/update.php --quick"+wikiFlag); err != nil {
		log.Fatalf("update.php failed%s: %v", wikiMsg, err)
	}

	if !skipJobs {
		fmt.Printf("Running runJobs.php%s...\n", wikiMsg)
		if err := orchestrators.ExecStreaming(instance.Path, instance.Orchestrator, "web",
			"php maintenance/runJobs.php"+wikiFlag); err != nil {
			log.Fatalf("runJobs.php failed%s: %v", wikiMsg, err)
		}
	}

	if !skipSMW {
		smwCheck := fmt.Sprintf(
			`php maintenance/eval.php%s --expression="echo defined('SMW_VERSION') ? 'yes' : 'no';"`,
			wikiFlag)
		smwOutput, smwErr := orchestrators.ExecWithError(instance.Path, instance.Orchestrator, "web", smwCheck)
		if smwErr != nil || !strings.Contains(smwOutput, "yes") {
			fmt.Printf("Semantic MediaWiki not enabled%s, skipping rebuildData.php\n", wikiMsg)
		} else {
			fmt.Printf("Running rebuildData.php%s...\n", wikiMsg)
			if err := orchestrators.ExecStreaming(instance.Path, instance.Orchestrator, "web",
				"php extensions/SemanticMediaWiki/maintenance/rebuildData.php"+wikiFlag); err != nil {
				log.Fatalf("rebuildData.php failed%s: %v", wikiMsg, err)
			}
		}
	}

	fmt.Printf("Completed maintenance%s\n", wikiMsg)
}
