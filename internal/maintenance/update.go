package maintenance

import (
	"fmt"
	"path/filepath"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// GetWikiIDs returns the list of wiki IDs from the instance's wikis.yaml.
func GetWikiIDs(instance config.Installation) ([]string, error) {
	yamlPath := filepath.Join(instance.Path, "config", "wikis.yaml")
	ids, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read wikis.yaml: %v", err)
	}
	return ids, nil
}

// RunUpdatePhp runs update.php --quick for a single wiki.
func RunUpdatePhp(instance config.Installation, orch orchestrators.Orchestrator, wikiID string) error {
	wikiFlag := ""
	if wikiID != "" {
		wikiFlag = " --wiki=" + wikiID
	}
	wikiMsg := ""
	if wikiID != "" {
		wikiMsg = " for wiki '" + wikiID + "'"
	}

	fmt.Printf("Running update.php%s...\n", wikiMsg)
	if err := orch.ExecStreaming(instance.Path, orchestrators.ServiceWeb,
		"php maintenance/update.php --quick"+wikiFlag); err != nil {
		return fmt.Errorf("update.php failed%s: %v", wikiMsg, err)
	}
	return nil
}

// RunUpdateAllWikis runs update.php --quick. If wiki is non-empty, runs only
// for that wiki. Otherwise runs for all wikis from wikis.yaml.
func RunUpdateAllWikis(instance config.Installation, orch orchestrators.Orchestrator, wiki string) error {
	if wiki != "" {
		return RunUpdatePhp(instance, orch, wiki)
	}
	wikiIDs, err := GetWikiIDs(instance)
	if err != nil {
		return err
	}
	for _, id := range wikiIDs {
		if err := RunUpdatePhp(instance, orch, id); err != nil {
			return err
		}
	}
	return nil
}
