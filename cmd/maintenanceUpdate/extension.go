package maintenance

import (
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func extensionCmdCreate() *cobra.Command {

	extensionCmd := &cobra.Command{
		Use:   `extension [extension-name] ["script.php [args]"]`,
		Short: "Run extension maintenance scripts",
		Long: `Run maintenance scripts provided by MediaWiki extensions.

With no arguments, lists all extensions that have a maintenance/ directory.
With one argument (extension name), lists available maintenance scripts for
that extension. With two arguments (extension name and a quoted script string),
runs the specified script. Any arguments to the script should be included in
the quoted string.

In a wiki farm, use --wiki to target a specific wiki, or --all to run
on every wiki.`,
		Example: `  # List extensions with maintenance scripts
  canasta maintenance extension -i myinstance

  # List maintenance scripts for an extension
  canasta maintenance extension SemanticMediaWiki -i myinstance

  # Run an extension maintenance script
  canasta maintenance extension SemanticMediaWiki "rebuildData.php" -i myinstance

  # Run with script arguments (in quotes)
  canasta maintenance extension SemanticMediaWiki "rebuildData.php -s 1000 -e 2000" -i myinstance

  # Run for a specific wiki in a farm
  canasta maintenance extension CirrusSearch "UpdateSearchIndexConfig.php" -i myinstance --wiki=docs

  # Run for all wikis
  canasta maintenance extension SemanticMediaWiki "rebuildData.php" -i myinstance --all`,
		Args: cobra.RangeArgs(0, 2),
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			switch len(args) {
			case 0:
				return listExtensionsWithMaintenance(instance)
			case 1:
				return listExtensionScripts(instance, args[0])
			case 2:
				if wiki != "" && all {
					return fmt.Errorf("cannot use --wiki with --all")
				}
				if all {
					wikiIDs, err := getWikiIDs(instance)
					if err != nil {
						return err
					}
					for _, id := range wikiIDs {
						if err := runExtensionScript(instance, args[0], args[1], id); err != nil {
							return err
						}
					}
					return nil
				}
				return runExtensionScript(instance, args[0], args[1], wiki)
			}
			return nil
		},
	}

	return extensionCmd
}

func listExtensionsWithMaintenance(inst config.Installation) error {
	return listExtensionsWithMaintenanceWith(nil, inst)
}

func listExtensionsWithMaintenanceWith(orch orchestrators.Orchestrator, inst config.Installation) error {
	if orch == nil {
		var err error
		orch, err = orchestrators.New(inst.Orchestrator)
		if err != nil {
			return err
		}
	}

	cmd := `find extensions/ canasta-extensions/ -maxdepth 2 -name maintenance -type d 2>/dev/null`
	output, _ := orch.ExecWithError(inst.Path, "web", cmd)

	names := parseExtensionNames(output)
	if len(names) == 0 {
		fmt.Println("No extensions with maintenance scripts found.")
		return nil
	}

	fmt.Println("Extensions with maintenance scripts:")
	for _, name := range names {
		fmt.Printf("  %s\n", name)
	}
	return nil
}

func listExtensionScripts(inst config.Installation, extName string) error {
	return listExtensionScriptsWith(nil, inst, extName)
}

func listExtensionScriptsWith(orch orchestrators.Orchestrator, inst config.Installation, extName string) error {
	if orch == nil {
		var err error
		orch, err = orchestrators.New(inst.Orchestrator)
		if err != nil {
			return err
		}
	}

	// Check that the extension has a maintenance directory
	checkCmd := fmt.Sprintf(
		`test -d extensions/%s/maintenance && echo exists || test -d canasta-extensions/%s/maintenance && echo exists`,
		extName, extName)
	checkOutput, _ := orch.ExecWithError(inst.Path, "web", checkCmd)
	if !strings.Contains(checkOutput, "exists") {
		return fmt.Errorf("extension %q has no maintenance directory", extName)
	}

	cmd := fmt.Sprintf(
		`find extensions/%s/maintenance/ canasta-extensions/%s/maintenance/ -maxdepth 1 -name '*.php' -type f 2>/dev/null`,
		extName, extName)
	output, _ := orch.ExecWithError(inst.Path, "web", cmd)

	scripts := parseScriptNames(output)
	if len(scripts) == 0 {
		fmt.Printf("No maintenance scripts found for %s.\n", extName)
		return nil
	}

	fmt.Printf("Maintenance scripts for %s:\n", extName)
	for _, script := range scripts {
		fmt.Printf("  %s\n", script)
	}
	return nil
}

func runExtensionScript(inst config.Installation, extName, scriptStr, wikiID string) error {
	return runExtensionScriptWith(nil, inst, extName, scriptStr, wikiID)
}

func runExtensionScriptWith(orch orchestrators.Orchestrator, inst config.Installation, extName, scriptStr, wikiID string) error {
	if orch == nil {
		var err error
		orch, err = orchestrators.New(inst.Orchestrator)
		if err != nil {
			return err
		}
	}

	// Determine which path the extension is at
	extPath := ""
	for _, prefix := range []string{"extensions", "canasta-extensions"} {
		checkCmd := fmt.Sprintf("test -d %s/%s/maintenance && echo exists", prefix, extName)
		checkOutput, _ := orch.ExecWithError(inst.Path, "web", checkCmd)
		if strings.Contains(checkOutput, "exists") {
			extPath = prefix + "/" + extName
			break
		}
	}
	if extPath == "" {
		return fmt.Errorf("extension %q has no maintenance directory", extName)
	}

	wikiFlag := ""
	wikiMsg := ""
	if wikiID != "" {
		wikiFlag = " --wiki=" + wikiID
		wikiMsg = " for wiki '" + wikiID + "'"
	}

	cmd := "php " + extPath + "/maintenance/" + scriptStr + wikiFlag

	fmt.Printf("Running %s%s...\n", scriptStr, wikiMsg)
	if err := orch.ExecStreaming(inst.Path, "web", cmd); err != nil {
		return fmt.Errorf("%s failed%s: %v", scriptStr, wikiMsg, err)
	}

	fmt.Printf("Completed %s%s\n", scriptStr, wikiMsg)
	return nil
}

// parseExtensionNames extracts extension names from find output like:
//
//	extensions/Foo/maintenance
//	canasta-extensions/Bar/maintenance
func parseExtensionNames(output string) []string {
	seen := make(map[string]bool)
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		// Extract extension name from path like "extensions/Foo/maintenance"
		parts := strings.Split(line, "/")
		if len(parts) >= 3 {
			name := parts[1]
			seen[name] = true
		}
	}

	names := make([]string, 0, len(seen))
	for name := range seen {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// parseScriptNames extracts script filenames from find output like:
//
//	extensions/Foo/maintenance/rebuildData.php
func parseScriptNames(output string) []string {
	seen := make(map[string]bool)
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		name := filepath.Base(line)
		if strings.HasSuffix(name, ".php") {
			seen[name] = true
		}
	}

	scripts := make([]string, 0, len(seen))
	for name := range seen {
		scripts = append(scripts, name)
	}
	sort.Strings(scripts)
	return scripts
}
