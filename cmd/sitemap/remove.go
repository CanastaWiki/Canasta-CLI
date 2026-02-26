package sitemap

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newRemoveCmd(instance *config.Installation, orch *orchestrators.Orchestrator) *cobra.Command {
	var wikiID string
	var yes bool

	cmd := &cobra.Command{
		Use:   "remove",
		Short: "Remove sitemaps for one or all wikis",
		Long: `Remove XML sitemap files for wikis in a Canasta installation. If --wiki is
specified, removes the sitemap for that wiki only. Otherwise, removes sitemaps
for all wikis. Once removed, the background generator will skip those wikis.`,
		Example: `  # Remove sitemap for a specific wiki
  canasta sitemap remove -i myinstance -w mywiki

  # Remove sitemaps for all wikis
  canasta sitemap remove -i myinstance

  # Remove sitemaps for all wikis without prompting
  canasta sitemap remove -i myinstance -y`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return runRemove(*instance, *orch, wikiID, yes)
		},
	}

	cmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "Wiki ID (omit to remove for all wikis)")
	cmd.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation prompt")

	return cmd
}

func runRemove(instance config.Installation, orch orchestrators.Orchestrator, wikiID string, yes bool) error {
	// Check containers are running
	if err := orch.CheckRunningStatus(instance); err != nil {
		return fmt.Errorf("containers are not running: %w", err)
	}

	yamlPath := filepath.Join(instance.Path, "config", "wikis.yaml")
	ids, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return fmt.Errorf("failed to read wikis.yaml: %w", err)
	}

	removingAll := wikiID == ""
	var wikiIDs []string

	if removingAll {
		if !yes {
			reader := bufio.NewReader(os.Stdin)
			fmt.Print("Remove sitemaps for all wikis? [y/N] ")
			text, _ := reader.ReadString('\n')
			text = strings.ToLower(strings.TrimSpace(text))
			if text != "y" {
				fmt.Println("Operation cancelled.")
				return nil
			}
		}
		wikiIDs = ids
	} else {
		// Validate wiki exists
		found := false
		for _, id := range ids {
			if id == wikiID {
				found = true
				break
			}
		}
		if !found {
			return fmt.Errorf("wiki '%s' not found in wikis.yaml", wikiID)
		}
		wikiIDs = []string{wikiID}
	}

	for _, id := range wikiIDs {
		fspath := "/mediawiki/public_assets/" + id + "/sitemap"
		rmCmd := fmt.Sprintf("find %s -mindepth 1 -delete 2>/dev/null; true", fspath)
		if _, err := orch.ExecWithError(instance.Path, orchestrators.ServiceWeb, rmCmd); err != nil {
			return fmt.Errorf("failed to remove sitemap files for wiki '%s': %w", id, err)
		}
		fmt.Printf("Removed sitemap for wiki '%s'.\n", id)
	}

	return nil
}
