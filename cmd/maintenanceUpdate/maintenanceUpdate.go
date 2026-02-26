package maintenance

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	internalmaintenance "github.com/CanastaWiki/Canasta-CLI/internal/maintenance"
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
		RunE: func(cmd *cobra.Command, args []string) error {
			if wiki != "" && all {
				return fmt.Errorf("cannot use --wiki with --all")
			}
			opts := internalmaintenance.Options{SkipJobs: skipJobs, SkipSMW: skipSMW}
			if all {
				return internalmaintenance.RunUpdateAllWikis(instance, opts)
			} else if wiki != "" {
				return internalmaintenance.RunMaintenanceUpdate(instance, wiki, opts)
			} else {
				wikiIDs, err := internalmaintenance.GetWikiIDs(instance)
				if err != nil {
					return err
				}
				if len(wikiIDs) == 1 {
					return internalmaintenance.RunMaintenanceUpdate(instance, wikiIDs[0], opts)
				} else {
					return fmt.Errorf("multiple wikis found; use --wiki=<id> or --all")
				}
			}
		},
	}

	updateCmd.Flags().BoolVar(&skipJobs, "skip-jobs", false, "Skip running runJobs.php")
	updateCmd.Flags().BoolVar(&skipSMW, "skip-smw", false, "Skip running Semantic MediaWiki rebuildData.php")

	return updateCmd
}
