package maintenance

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

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
	wikiIDs, err := farmsettings.GetWikiIDs(instance.Path)
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
