package sitemap

import (
	"fmt"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
)

func generateCmdCreate() *cobra.Command {
	var wikiID string

	cmd := &cobra.Command{
		Use:   "generate",
		Short: "Generate sitemaps for one or all wikis",
		Long: `Generate XML sitemaps for wikis in a Canasta installation. If --wiki is
specified, generates a sitemap for that wiki only. Otherwise, generates
sitemaps for all wikis in the instance. Once generated, the background
generator will automatically refresh them.`,
		Example: `  # Generate sitemap for a specific wiki
  canasta sitemap generate -i myinstance -w mywiki

  # Generate sitemaps for all wikis
  canasta sitemap generate -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return runGenerate(wikiID)
		},
	}

	cmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "Wiki ID (omit to generate for all wikis)")

	return cmd
}

func runGenerate(wikiID string) error {
	// Check containers are running
	if err := orch.CheckRunningStatus(instance); err != nil {
		return fmt.Errorf("containers are not running: %w", err)
	}

	yamlPath := filepath.Join(instance.Path, "config", "wikis.yaml")
	ids, serverNames, paths, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return fmt.Errorf("failed to read wikis.yaml: %w", err)
	}

	// Get URL scheme from MW_SITE_SERVER in .env
	envPath := filepath.Join(instance.Path, ".env")
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return fmt.Errorf("failed to read .env: %w", err)
	}
	scheme := extractScheme(envVars["MW_SITE_SERVER"])

	// Get wgScriptPath from inside the container
	scriptPathCmd := `php /getMediawikiSettings.php --variable="wgScriptPath" --format="string"`
	scriptPath, err := orch.ExecWithError(instance.Path, "web", scriptPathCmd)
	if err != nil {
		return fmt.Errorf("failed to get wgScriptPath: %w", err)
	}

	// Determine which wikis to generate for
	type wikiInfo struct {
		id         string
		serverName string
		path       string
	}
	var wikis []wikiInfo

	if wikiID != "" {
		// Validate wiki exists
		found := false
		for idx, id := range ids {
			if id == wikiID {
				wikis = append(wikis, wikiInfo{id: id, serverName: serverNames[idx], path: paths[idx]})
				found = true
				break
			}
		}
		if !found {
			return fmt.Errorf("wiki '%s' not found in wikis.yaml", wikiID)
		}
	} else {
		// All wikis
		for idx, id := range ids {
			wikis = append(wikis, wikiInfo{id: id, serverName: serverNames[idx], path: paths[idx]})
		}
	}

	for _, wiki := range wikis {
		// Build the server URL
		serverURL := scheme + "://" + wiki.serverName
		if wiki.path != "/" {
			serverURL += wiki.path
		}

		fspath := "/mediawiki/public_assets/" + wiki.id + "/sitemap"

		// Create the sitemap directory
		mkdirCmd := fmt.Sprintf("mkdir -p %s && chown www-data:www-data %s", fspath, fspath)
		if _, err := orch.ExecWithError(instance.Path, "web", mkdirCmd); err != nil {
			return fmt.Errorf("failed to create sitemap directory for wiki '%s': %w", wiki.id, err)
		}

		// Run generateSitemap.php
		fmt.Printf("Generating sitemap for wiki '%s'...\n", wiki.id)
		genCmd := fmt.Sprintf(
			"php /mediawiki/maintenance/generateSitemap.php --wiki=%s --fspath=%s --urlpath=%s/public_assets/sitemap --compress yes --server=%s --skip-redirects --identifier=%s",
			wiki.id, fspath, scriptPath, serverURL, wiki.id,
		)
		if err := orch.ExecStreaming(instance.Path, "web", genCmd); err != nil {
			return fmt.Errorf("failed to generate sitemap for wiki '%s': %w", wiki.id, err)
		}
		fmt.Printf("Sitemap generated for wiki '%s'.\n", wiki.id)
	}

	fmt.Println("Sitemaps will be refreshed automatically.")
	return nil
}

// extractScheme returns the URL scheme from a server URL, defaulting to "https".
func extractScheme(serverURL string) string {
	if len(serverURL) >= 8 && serverURL[:8] == "https://" {
		return "https"
	}
	if len(serverURL) >= 7 && serverURL[:7] == "http://" {
		return "http"
	}
	return "https"
}
