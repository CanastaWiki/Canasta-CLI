package maintenance

import (
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	maint "github.com/CanastaWiki/Canasta-CLI/internal/maintenance"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newUpdateCmd(instance *config.Installation, wiki *string) *cobra.Command {
	var (
		skipJobs bool
		skipSMW  bool
	)

	updateCmd := &cobra.Command{
		Use:   "update",
		Short: "Run maintenance update jobs",
		Long: `Run the standard MediaWiki maintenance update sequence: update.php,
runJobs.php, and Semantic MediaWiki's rebuildData.php. This is typically
needed after upgrading MediaWiki or enabling new extensions.

By default, all three scripts are run. Use --skip-jobs to skip runJobs.php
and --skip-smw to skip rebuildData.php.

In a wiki farm, runs on all wikis by default. Use --wiki to target a
specific wiki.`,
		Example: `  canasta maintenance update -i myinstance
  canasta maintenance update -i myinstance --wiki=docs
  canasta maintenance update -i myinstance --skip-jobs --skip-smw`,
		PreRunE: func(cmd *cobra.Command, args []string) error {
			var err error
			*instance, err = canasta.CheckCanastaId(*instance)
			return err
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			if *wiki != "" {
				return runMaintenanceUpdate(*instance, *wiki, skipJobs, skipSMW)
			}
			wikiIDs, err := farmsettings.GetWikiIDs(instance.Path)
			if err != nil {
				return err
			}
			for _, id := range wikiIDs {
				if err := runMaintenanceUpdate(*instance, id, skipJobs, skipSMW); err != nil {
					return err
				}
			}
			return nil
		},
	}

	updateCmd.Flags().BoolVar(&skipJobs, "skip-jobs", false, "Skip running runJobs.php")
	updateCmd.Flags().BoolVar(&skipSMW, "skip-smw", false, "Skip running Semantic MediaWiki rebuildData.php")

	return updateCmd
}

func runMaintenanceUpdate(instance config.Installation, wikiID string, skipJobs, skipSMW bool) error {
	orch, err := orchestrators.New(instance.Orchestrator)
	if err != nil {
		return err
	}

	if err := maint.RunUpdatePhp(instance, orch, wikiID); err != nil {
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

	if !skipJobs {
		fmt.Printf("Running runJobs.php%s...\n", wikiMsg)
		if err := orch.ExecStreaming(instance.Path, orchestrators.ServiceWeb,
			"php maintenance/runJobs.php"+wikiFlag); err != nil {
			return fmt.Errorf("runJobs.php failed%s: %v", wikiMsg, err)
		}
	}

	if !skipSMW {
		const rebuildScript = "extensions/SemanticMediaWiki/maintenance/rebuildData.php"
		checkCmd := fmt.Sprintf("test -f %s && echo exists", rebuildScript)
		checkOutput, _ := orch.ExecWithError(instance.Path, orchestrators.ServiceWeb, checkCmd)
		if !strings.Contains(checkOutput, "exists") {
			fmt.Printf("Semantic MediaWiki not installed%s, skipping rebuildData.php\n", wikiMsg)
		} else {
			fmt.Printf("Running rebuildData.php%s...\n", wikiMsg)
			if err := orch.ExecStreaming(instance.Path, orchestrators.ServiceWeb,
				"php "+rebuildScript+wikiFlag); err != nil {
				fmt.Printf("rebuildData.php failed%s: %v\n", wikiMsg, err)
			}
		}
	}

	fmt.Printf("Completed maintenance%s\n", wikiMsg)
	return nil
}
