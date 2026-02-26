package maintenance

import (
	"fmt"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// wikiArgRe matches --wiki=value or --wiki value in a script argument string.
var wikiArgRe = regexp.MustCompile(`(?:^|\s)--wiki[=\s](\S+)`)

func newExtensionCmd() *cobra.Command {

	extensionCmd := &cobra.Command{
		Use:   "extension [extension-name] [script.php [args...]]",
		Short: "Run extension maintenance scripts",
		Long: `Run maintenance scripts provided by loaded MediaWiki extensions.

With no arguments, lists all loaded extensions that have a maintenance/
directory. With one argument (extension name), lists available maintenance
scripts for that extension. With two or more arguments (extension name,
script name, and optional script arguments), runs the specified script.

Flags (-i, --wiki, --all) must come before the extension name. Everything
after the extension name is treated as the script and its arguments — no
quotes are needed.

Only extensions that are currently loaded (enabled) for the target wiki are
shown and allowed to run. In a wiki farm, use --wiki to target a specific
wiki, or --all to run on every wiki. If there is only one wiki, it is
selected automatically.`,
		Example: `  # List loaded extensions with maintenance scripts
  canasta maintenance extension -i myinstance

  # List maintenance scripts for an extension
  canasta maintenance extension -i myinstance SemanticMediaWiki

  # Run an extension maintenance script
  canasta maintenance extension -i myinstance SemanticMediaWiki rebuildData.php

  # Run with script arguments (no quotes needed)
  canasta maintenance extension -i myinstance SemanticMediaWiki rebuildData.php -s 1000 -e 2000

  # Run for a specific wiki in a farm
  canasta maintenance extension -i myinstance --wiki=docs CirrusSearch UpdateSearchIndexConfig.php

  # Run for all wikis
  canasta maintenance extension -i myinstance --all SemanticMediaWiki rebuildData.php`,
		Args: cobra.ArbitraryArgs,
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			if wiki != "" && all {
				return fmt.Errorf("cannot use --wiki with --all")
			}
			switch {
			case len(args) == 0:
				return listExtensionsWithMaintenance(instance, wiki, all)
			case len(args) == 1:
				return listExtensionScripts(instance, args[0], wiki, all)
			default:
				extName := args[0]
				scriptStr := strings.Join(args[1:], " ")
				if all {
					wikiIDs, err := getWikiIDs(instance)
					if err != nil {
						return err
					}
					for _, id := range wikiIDs {
						if err := runExtensionScript(instance, extName, scriptStr, id); err != nil {
							return err
						}
					}
					return nil
				}
				return runExtensionScript(instance, extName, scriptStr, wiki)
			}
		},
	}

	// Stop parsing flags after the first non-flag argument (the extension name).
	// This allows script arguments like -s 1000 to be passed without quotes.
	extensionCmd.Flags().SetInterspersed(false)

	return extensionCmd
}

func listExtensionsWithMaintenance(inst config.Installation, wikiFlag string, allFlag bool) error {
	return listExtensionsWithMaintenanceWith(nil, inst, wikiFlag, allFlag)
}

func listExtensionsWithMaintenanceWith(orch orchestrators.Orchestrator, inst config.Installation, wikiFlag string, allFlag bool) error {
	if orch == nil {
		var err error
		orch, err = orchestrators.New(inst.Orchestrator)
		if err != nil {
			return err
		}
	}

	// Resolve which wiki(s) to query for loaded extensions
	wikiIDs, err := resolveWikiIDs(inst, wikiFlag, allFlag)
	if err != nil {
		return err
	}

	// Get loaded extensions across the target wiki(s)
	loaded := make(map[string]bool)
	for _, id := range wikiIDs {
		exts, err := getLoadedExtensions(orch, inst.Path, id)
		if err != nil {
			return fmt.Errorf("failed to query loaded extensions for wiki %q: %v", id, err)
		}
		for _, ext := range exts {
			loaded[ext] = true
		}
	}

	// Find extensions with maintenance directories
	cmd := `find extensions/ canasta-extensions/ -maxdepth 2 -name maintenance -type d 2>/dev/null`
	output, _ := orch.ExecWithError(inst.Path, orchestrators.ServiceWeb, cmd)

	names := parseExtensionNames(output)

	// Filter to only loaded extensions
	var filtered []string
	for _, name := range names {
		if loaded[name] {
			filtered = append(filtered, name)
		}
	}

	if len(filtered) == 0 {
		fmt.Println("No loaded extensions with maintenance scripts found")
		return nil
	}

	fmt.Println("Extensions with maintenance scripts:")
	for _, name := range filtered {
		fmt.Printf("  %s\n", name)
	}
	return nil
}

func listExtensionScripts(inst config.Installation, extName, wikiFlag string, allFlag bool) error {
	return listExtensionScriptsWith(nil, inst, extName, wikiFlag, allFlag)
}

func listExtensionScriptsWith(orch orchestrators.Orchestrator, inst config.Installation, extName, wikiFlag string, allFlag bool) error {
	if orch == nil {
		var err error
		orch, err = orchestrators.New(inst.Orchestrator)
		if err != nil {
			return err
		}
	}

	// Resolve which wiki(s) to check
	wikiIDs, err := resolveWikiIDs(inst, wikiFlag, allFlag)
	if err != nil {
		return err
	}

	// Check that the extension is loaded for at least one target wiki
	loaded := false
	for _, id := range wikiIDs {
		exts, err := getLoadedExtensions(orch, inst.Path, id)
		if err != nil {
			return fmt.Errorf("failed to query loaded extensions for wiki %q: %v", id, err)
		}
		for _, ext := range exts {
			if ext == extName {
				loaded = true
				break
			}
		}
		if loaded {
			break
		}
	}
	if !loaded {
		return fmt.Errorf("Extension %q is not loaded for the target wiki(s)", extName)
	}

	// Check that the extension has a maintenance directory
	checkCmd := fmt.Sprintf(
		`test -d extensions/%s/maintenance && echo exists || test -d canasta-extensions/%s/maintenance && echo exists`,
		extName, extName)
	checkOutput, _ := orch.ExecWithError(inst.Path, orchestrators.ServiceWeb, checkCmd)
	if !strings.Contains(checkOutput, "exists") {
		return fmt.Errorf("extension %q has no maintenance directory", extName)
	}

	cmd := fmt.Sprintf(
		`find extensions/%s/maintenance/ canasta-extensions/%s/maintenance/ -maxdepth 1 -name '*.php' -type f 2>/dev/null`,
		extName, extName)
	output, _ := orch.ExecWithError(inst.Path, orchestrators.ServiceWeb, cmd)

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

	// Reconcile --wiki from CLI flag and script string
	resolvedWiki, cleanedScript, err := resolveWikiFlag(wikiID, scriptStr)
	if err != nil {
		return err
	}

	// Resolve wiki ID if not provided (auto-detect for single-wiki installs)
	checkWiki := resolvedWiki
	if checkWiki == "" {
		wikiIDs, err := getWikiIDs(inst)
		if err != nil {
			return err
		}
		if len(wikiIDs) == 1 {
			checkWiki = wikiIDs[0]
		} else {
			return fmt.Errorf("multiple wikis found; use --wiki=<id> or --all")
		}
	}

	// Check that the extension is loaded for the target wiki
	exts, err := getLoadedExtensions(orch, inst.Path, checkWiki)
	if err != nil {
		return fmt.Errorf("failed to query loaded extensions for wiki %q: %v", checkWiki, err)
	}
	loaded := false
	for _, ext := range exts {
		if ext == extName {
			loaded = true
			break
		}
	}
	if !loaded {
		return fmt.Errorf("Extension %q is not loaded for wiki %q", extName, checkWiki)
	}

	// Determine which path the extension is at
	extPath := ""
	for _, prefix := range []string{"extensions", "canasta-extensions"} {
		checkCmd := fmt.Sprintf("test -d %s/%s/maintenance && echo exists", prefix, extName)
		checkOutput, _ := orch.ExecWithError(inst.Path, orchestrators.ServiceWeb, checkCmd)
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
	if resolvedWiki != "" {
		wikiFlag = " --wiki=" + resolvedWiki
		wikiMsg = " for wiki '" + resolvedWiki + "'"
	}

	cmd := "php " + extPath + "/maintenance/" + cleanedScript + wikiFlag

	fmt.Printf("Running %s%s...\n", cleanedScript, wikiMsg)
	if err := orch.ExecStreaming(inst.Path, orchestrators.ServiceWeb, cmd); err != nil {
		return fmt.Errorf("%s failed%s: %v", cleanedScript, wikiMsg, err)
	}

	fmt.Printf("Completed %s%s\n", cleanedScript, wikiMsg)
	return nil
}

// resolveWikiFlag reconciles a --wiki value from the CLI flag with a --wiki
// value that may be embedded in the script argument string. If both are present
// with different values, it returns an error. If both are present with the same
// value, or only one is present, it returns the resolved wiki ID and the script
// string with the embedded --wiki removed (to avoid passing it twice).
func resolveWikiFlag(cliWiki, scriptStr string) (resolvedWiki, cleanedScript string, err error) {
	match := wikiArgRe.FindStringSubmatch(scriptStr)
	if match == nil {
		// No --wiki in script string; use CLI flag as-is
		return cliWiki, scriptStr, nil
	}

	scriptWiki := match[1]
	cleanedScript = strings.TrimSpace(wikiArgRe.ReplaceAllString(scriptStr, ""))

	if cliWiki == "" {
		return scriptWiki, cleanedScript, nil
	}
	if cliWiki != scriptWiki {
		return "", "", fmt.Errorf("conflicting --wiki values: flag has %q but script string has %q", cliWiki, scriptWiki)
	}
	return cliWiki, cleanedScript, nil
}

// resolveWikiIDs returns the list of wiki IDs to operate on. For a single-wiki
// install it auto-detects; for a farm it requires --wiki or --all.
func resolveWikiIDs(inst config.Installation, wikiFlag string, allFlag bool) ([]string, error) {
	if wikiFlag != "" {
		return []string{wikiFlag}, nil
	}
	wikiIDs, err := getWikiIDs(inst)
	if err != nil {
		return nil, err
	}
	if allFlag || len(wikiIDs) == 1 {
		return wikiIDs, nil
	}
	return nil, fmt.Errorf("multiple wikis found; use --wiki=<id> or --all")
}

// getLoadedExtensions queries MediaWiki for the list of extensions currently
// loaded for the given wiki. It uses eval.php to call ExtensionRegistry.
func getLoadedExtensions(orch orchestrators.Orchestrator, installPath, wikiID string) ([]string, error) {
	cmd := fmt.Sprintf(
		`echo 'echo implode(PHP_EOL, array_keys(ExtensionRegistry::getInstance()->getAllThings()));' | php maintenance/eval.php --wiki=%s 2>/dev/null`,
		wikiID)
	output, err := orch.ExecWithError(installPath, orchestrators.ServiceWeb, cmd)
	if err != nil {
		return nil, err
	}
	return parseLoadedExtensions(output), nil
}

// parseLoadedExtensions parses the output of ExtensionRegistry::getAllThings()
// into a list of extension/skin names.
func parseLoadedExtensions(output string) []string {
	var names []string
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		names = append(names, line)
	}
	return names
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
