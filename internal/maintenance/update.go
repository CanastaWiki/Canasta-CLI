package maintenance

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

type Options struct {
	SkipJobs bool
	SkipSMW bool
}

func GetWikiIDs(instance config.Installation) ([]string, error) {
	yamlPath := filepath.Join(instance.Path, "config", "wikis.yaml")
	ids, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read wikis.yaml: %v", err)
	}
	return ids, nil
}

func RunMaintenanceUpdate(instance config.Installation, wikiID string, opts Options) error {
	orch, err := orchestrators.New(instance.Orchestrator)
	if err != nil {
		return err
	}
	wikiFlag := ""
	wikiMsg := ""
	if wikiID != "" {
		wikiFlag = " --wiki=" + wikiID
		wikiMsg = " for wiki '" + wikiID + "'"
	}

	fmt.Printf("Running update.php%s...\n", wikiMsg)
	if err := orch.ExecStreaming(instance.Path, "web",
		"php maintenance/update.php --quick"+wikiFlag); err != nil {
		return fmt.Errorf("update.php failed%s: %v", wikiMsg, err)
	}

	if !opts.SkipJobs {
		fmt.Printf("Running runJobs.php%s...\n", wikiMsg)
		if err := orch.ExecStreaming(instance.Path, "web",
			"php maintenance/runJobs.php"+wikiFlag); err != nil {
			return fmt.Errorf("runJobs.php failed%s: %v", wikiMsg, err)
		}
	}

	if !opts.SkipSMW {
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

func RunUpdateAllWikis(instance config.Installation, opts Options) error {
	wikiIDs, err := GetWikiIDs(instance)
	if err != nil {
		return err
	}
	for _, id := range wikiIDs {
		if err := RunMaintenanceUpdate(instance, id, opts); err != nil {
			return err
		}
	}
	return nil
}
