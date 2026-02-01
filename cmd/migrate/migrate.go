package migrate

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
)

var instance config.Installation
var dryRun bool

func NewCmdCreate() *cobra.Command {
	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	var migrateCmd = &cobra.Command{
		Use:   "migrate",
		Short: "Migrate config files to the new directory structure",
		Long: `Migrate config files from the legacy directory structure to the new structure.

Legacy structure:
  config/settings/*.php (global settings)
  config/<wiki_id>/ (per-wiki settings)

New structure:
  config/settings/global/*.php (global settings)
  config/settings/wikis/<wiki_id>/ (per-wiki settings)`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			return Migrate(instance, dryRun)
		},
	}
	migrateCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	migrateCmd.Flags().BoolVar(&dryRun, "dry-run", false, "Show what would be moved without actually moving files")
	return migrateCmd
}

// HasLegacyStructure checks if the installation uses the legacy directory structure
func HasLegacyStructure(installPath string) (bool, error) {
	// Check for legacy per-wiki directories (config/<wiki_id>/)
	yamlPath := filepath.Join(installPath, "config", "wikis.yaml")
	wikiIDs, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return false, nil // No wikis.yaml, can't determine
	}

	for _, wikiID := range wikiIDs {
		// Normalize wikiID (same as in canasta.go)
		id := strings.Replace(wikiID, " ", "_", -1)
		id = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(id, "")

		legacyPath := filepath.Join(installPath, "config", id)
		newPath := filepath.Join(installPath, "config", "settings", "wikis", id)

		// If legacy path exists and new path doesn't, it's legacy structure
		if info, err := os.Stat(legacyPath); err == nil && info.IsDir() {
			if _, err := os.Stat(newPath); os.IsNotExist(err) {
				return true, nil
			}
		}
	}

	// Check for legacy global settings (config/settings/*.php but not in global/)
	settingsPath := filepath.Join(installPath, "config", "settings")
	globalPath := filepath.Join(settingsPath, "global")

	entries, err := os.ReadDir(settingsPath)
	if err != nil {
		return false, nil
	}

	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".php") {
			// Found a .php file directly in config/settings/ (not in global/)
			// Check if global/ doesn't have this file
			globalFile := filepath.Join(globalPath, entry.Name())
			if _, err := os.Stat(globalFile); os.IsNotExist(err) {
				return true, nil
			}
		}
	}

	return false, nil
}

func Migrate(instance config.Installation, dryRun bool) error {
	var err error

	// Check installation existence
	instance, err = canasta.CheckCanastaId(instance)
	if err != nil {
		return err
	}

	if dryRun {
		fmt.Println("Dry run mode - no changes will be made")
	}

	fmt.Printf("Migrating config files for '%s'...\n", instance.Id)

	migratedAny := false

	// Migrate per-wiki settings
	yamlPath := filepath.Join(instance.Path, "config", "wikis.yaml")
	wikiIDs, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return fmt.Errorf("failed to read wikis.yaml: %w", err)
	}

	for _, wikiID := range wikiIDs {
		// Normalize wikiID (same as in canasta.go)
		id := strings.Replace(wikiID, " ", "_", -1)
		id = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(id, "")

		legacyPath := filepath.Join(instance.Path, "config", id)
		newPath := filepath.Join(instance.Path, "config", "settings", "wikis", id)

		// Check if legacy path exists and new path doesn't
		if info, err := os.Stat(legacyPath); err == nil && info.IsDir() {
			if _, err := os.Stat(newPath); os.IsNotExist(err) {
				fmt.Printf("  Moving %s -> %s\n", legacyPath, newPath)
				if !dryRun {
					// Create parent directory
					if err := os.MkdirAll(filepath.Dir(newPath), os.ModePerm); err != nil {
						return fmt.Errorf("failed to create directory: %w", err)
					}
					// Move directory
					if err := os.Rename(legacyPath, newPath); err != nil {
						return fmt.Errorf("failed to move %s: %w", legacyPath, err)
					}
				}
				migratedAny = true
			}
		}
	}

	// Migrate global settings
	settingsPath := filepath.Join(instance.Path, "config", "settings")
	globalPath := filepath.Join(settingsPath, "global")

	entries, err := os.ReadDir(settingsPath)
	if err == nil {
		for _, entry := range entries {
			if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".php") {
				legacyFile := filepath.Join(settingsPath, entry.Name())
				newFile := filepath.Join(globalPath, entry.Name())

				// Check if file doesn't exist in global/
				if _, err := os.Stat(newFile); os.IsNotExist(err) {
					fmt.Printf("  Moving %s -> %s\n", legacyFile, newFile)
					if !dryRun {
						// Create global directory if needed
						if err := os.MkdirAll(globalPath, os.ModePerm); err != nil {
							return fmt.Errorf("failed to create directory: %w", err)
						}
						// Move file
						if err := os.Rename(legacyFile, newFile); err != nil {
							return fmt.Errorf("failed to move %s: %w", legacyFile, err)
						}
					}
					migratedAny = true
				}
			}
		}
	}

	if !migratedAny {
		fmt.Println("No files need to be migrated.")
	} else if dryRun {
		fmt.Println("Run without --dry-run to apply these changes.")
	} else {
		fmt.Println("Migration complete!")
	}

	return nil
}
