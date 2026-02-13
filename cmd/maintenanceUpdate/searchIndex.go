package maintenance

import (
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var startOver bool

func searchIndexCmdCreate() *cobra.Command {

	searchIndexCmd := &cobra.Command{
		Use:   "search-index",
		Short: "Rebuild CirrusSearch Elasticsearch indexes",
		Long: `Rebuild CirrusSearch Elasticsearch indexes for a Canasta installation.
This runs the three CirrusSearch maintenance scripts in sequence:
  1. UpdateSearchIndexConfig.php  (configure index mappings)
  2. ForceSearchIndex.php --skipLinks --indexOnSkip  (index content)
  3. ForceSearchIndex.php --skipParse  (index links)

By default, the index configuration is updated in place. Use --start-over
to destroy and recreate the index from scratch.

Requires the CirrusSearch and Elastica extensions to be installed.

In a wiki farm, use --wiki to target a specific wiki, or --all to rebuild
indexes for every wiki. If there is only one wiki, it is selected
automatically.`,
		Example: `  canasta maintenance search-index -i myinstance
  canasta maintenance search-index -i myinstance --start-over
  canasta maintenance search-index -i myinstance --wiki=docs
  canasta maintenance search-index -i myinstance --all`,
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
					if err := runSearchIndex(instance, id); err != nil {
						return err
					}
				}
			} else if wiki != "" {
				if err := runSearchIndex(instance, wiki); err != nil {
					return err
				}
			} else {
				wikiIDs, err := getWikiIDs(instance)
				if err != nil {
					return err
				}
				if len(wikiIDs) == 1 {
					if err := runSearchIndex(instance, wikiIDs[0]); err != nil {
						return err
					}
				} else {
					return fmt.Errorf("multiple wikis found; use --wiki=<id> or --all")
				}
			}
			return nil
		},
	}

	searchIndexCmd.Flags().BoolVar(&startOver, "start-over", false, "Destroy and recreate the index from scratch (uses --startOver flag)")

	return searchIndexCmd
}

func runSearchIndex(instance config.Installation, wikiID string) error {
	orch, err := orchestrators.New(instance.Orchestrator)
	if err != nil {
		return err
	}
	return runSearchIndexWith(orch, instance.Path, wikiID, startOver)
}

func runSearchIndexWith(orch orchestrators.Orchestrator, installPath string, wikiID string, startOverFlag bool) error {
	wikiFlag := ""
	if wikiID != "" {
		wikiFlag = " --wiki=" + wikiID
	}
	wikiMsg := ""
	if wikiID != "" {
		wikiMsg = " for wiki '" + wikiID + "'"
	}

	// Check that CirrusSearch is installed
	const updateScript = "extensions/CirrusSearch/maintenance/UpdateSearchIndexConfig.php"
	checkCmd := fmt.Sprintf("test -f %s && echo exists", updateScript)
	checkOutput, _ := orch.ExecWithError(installPath, "web", checkCmd)
	if !strings.Contains(checkOutput, "exists") {
		return fmt.Errorf("CirrusSearch extension is not installed; cannot rebuild search index%s", wikiMsg)
	}

	// Step 1: Update search index configuration
	updateFlags := " --reindexAndRemoveOk --indexIdentifier now"
	if startOverFlag {
		updateFlags = " --startOver"
	}
	fmt.Printf("Updating search index configuration%s...\n", wikiMsg)
	if err := orch.ExecStreaming(installPath, "web",
		"php "+updateScript+updateFlags+wikiFlag); err != nil {
		return fmt.Errorf("UpdateSearchIndexConfig.php failed%s: %v", wikiMsg, err)
	}

	// Step 2: Force index content (skip links)
	const forceScript = "extensions/CirrusSearch/maintenance/ForceSearchIndex.php"
	fmt.Printf("Indexing content%s...\n", wikiMsg)
	if err := orch.ExecStreaming(installPath, "web",
		"php "+forceScript+" --skipLinks --indexOnSkip"+wikiFlag); err != nil {
		return fmt.Errorf("ForceSearchIndex.php (content) failed%s: %v", wikiMsg, err)
	}

	// Step 3: Force index links (skip parse)
	fmt.Printf("Indexing links%s...\n", wikiMsg)
	if err := orch.ExecStreaming(installPath, "web",
		"php "+forceScript+" --skipParse"+wikiFlag); err != nil {
		return fmt.Errorf("ForceSearchIndex.php (links) failed%s: %v", wikiMsg, err)
	}

	fmt.Printf("Completed search index rebuild%s\n", wikiMsg)
	return nil
}
