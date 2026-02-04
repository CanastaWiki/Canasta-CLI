package upgrade

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/git"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var instance config.Installation
var dryRun bool
var upgradeAll bool

func NewCmdCreate() *cobra.Command {
	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	var upgradeCmd = &cobra.Command{
		Use:   "upgrade",
		Short: "Upgrade a Canasta installation to the latest version",
		Long: `Upgrade a Canasta installation by pulling the latest Docker Compose stack
and container images, running any necessary configuration migrations, and
restarting the containers. Use --dry-run to preview migrations without
applying them, or --all to upgrade every registered installation.`,
		Example: `  # Upgrade a single installation
  canasta upgrade -i myinstance

  # Preview what would change without applying
  canasta upgrade -i myinstance --dry-run

  # Upgrade all registered installations
  canasta upgrade --all`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if upgradeAll && instance.Id != "" {
				return fmt.Errorf("cannot use --all with --id")
			}
			if upgradeAll {
				return upgradeAllInstances(dryRun)
			}
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			if err := Upgrade(instance, dryRun); err != nil {
				return err
			}
			return nil
		},
	}
	upgradeCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	upgradeCmd.Flags().BoolVar(&dryRun, "dry-run", false, "Show what would change without applying")
	upgradeCmd.Flags().BoolVar(&upgradeAll, "all", false, "Upgrade all registered Canasta instances")
	return upgradeCmd
}

func upgradeAllInstances(dryRun bool) error {
	installations := config.GetAll()
	if len(installations) == 0 {
		fmt.Println("No registered installations found")
		return nil
	}

	// Sort by ID for deterministic output
	ids := make([]string, 0, len(installations))
	for id := range installations {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	var failedIDs []string
	for _, id := range ids {
		inst := installations[id]
		fmt.Printf("\n=== Upgrading instance '%s' ===\n", id)
		if err := Upgrade(inst, dryRun); err != nil {
			fmt.Printf("Error upgrading '%s': %s\n", id, err)
			failedIDs = append(failedIDs, id)
		}
	}

	succeeded := len(ids) - len(failedIDs)
	fmt.Printf("\nUpgraded %d/%d instances successfully\n", succeeded, len(ids))

	if len(failedIDs) > 0 {
		return fmt.Errorf("failed to upgrade: %s", strings.Join(failedIDs, ", "))
	}

	return nil
}

func Upgrade(instance config.Installation, dryRun bool) error {
	var err error

	// Check installation existence
	instance, err = canasta.CheckCanastaId(instance)
	if err != nil {
		return err
	}

	if dryRun {
		fmt.Println("Dry run mode - showing what would change without applying")
	}

	fmt.Println("Checking for repo changes...")
	if err = git.FetchAndCheckout(instance.Path, dryRun); err != nil {
		return err
	}

	if !dryRun {
		if err = orchestrators.Pull(instance.Path, instance.Orchestrator); err != nil {
			return err
		}
	}

	// Run migration steps (before restart so config is correct when containers come up)
	if err = runMigration(instance.Path, dryRun); err != nil {
		return err
	}

	if !dryRun {
		// Restart the containers
		if err = orchestrators.StopAndStart(instance); err != nil {
			return err
		}

		// Touch LocalSettings.php to flush cache
		fmt.Print("Running 'touch LocalSettings.php' to flush cache\n")
		_, err = orchestrators.ExecWithError(instance.Path, instance.Orchestrator, "web", "touch LocalSettings.php")
		if err != nil {
			return err
		}
		fmt.Print("Canasta Upgraded!\n")
	}

	return nil
}

// runMigration orchestrates all migration steps
func runMigration(installPath string, dryRun bool) error {
	fmt.Println("Checking for config migrations...")

	changed := false

	// Step 1: Extract or generate MW_SECRET_KEY
	keyChanged, err := extractSecretKey(installPath, dryRun)
	if err != nil {
		return err
	}
	if keyChanged {
		changed = true
	}

	// Step 2: Migrate directory structure
	dirChanged, err := migrateDirectoryStructure(installPath, dryRun)
	if err != nil {
		return err
	}
	if dirChanged {
		changed = true
	}

	// Step 3: Fix Vector.php default skin
	vectorChanged, err := fixVectorDefaultSkin(installPath, dryRun)
	if err != nil {
		return err
	}
	if vectorChanged {
		changed = true
	}

	if !changed {
		fmt.Println("No migrations needed.")
	} else if dryRun {
		fmt.Println("Run 'canasta upgrade' without --dry-run to apply these changes.")
	} else {
		fmt.Println("Migrations applied.")
	}

	return nil
}

// extractSecretKey extracts $wgSecretKey from PHP config files into .env as MW_SECRET_KEY,
// or generates a new one if not found
func extractSecretKey(installPath string, dryRun bool) (bool, error) {
	envPath := filepath.Join(installPath, ".env")

	// Check if MW_SECRET_KEY is already set in .env
	envVars := canasta.GetEnvVariable(envPath)
	if val, ok := envVars["MW_SECRET_KEY"]; ok && val != "" {
		fmt.Println("  MW_SECRET_KEY already set in .env, skipping extraction")
		return false, nil
	}

	// Search for $wgSecretKey in PHP config files
	phpFiles := []string{
		filepath.Join(installPath, "config", "LocalSettings.php"),
		filepath.Join(installPath, "config", "CommonSettings.php"),
	}

	// Also search per-wiki LocalSettings.php (where install.php writes $wgSecretKey)
	yamlPath := filepath.Join(installPath, "config", "wikis.yaml")
	wikiIDs, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err == nil {
		for _, wikiID := range wikiIDs {
			id := strings.Replace(wikiID, " ", "_", -1)
			id = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(id, "")
			// Check new path first, fall back to legacy (same pattern as canasta.go)
			newPath := filepath.Join(installPath, "config", "settings", "wikis", id, "LocalSettings.php")
			legacyPath := filepath.Join(installPath, "config", id, "LocalSettings.php")
			if _, err := os.Stat(newPath); err == nil {
				phpFiles = append(phpFiles, newPath)
			} else {
				phpFiles = append(phpFiles, legacyPath)
			}
		}
	}

	secretKeyRegex := regexp.MustCompile(`\$wgSecretKey\s*=\s*["']([a-f0-9]+)["']`)

	for _, phpFile := range phpFiles {
		content, err := os.ReadFile(phpFile)
		if err != nil {
			continue // File doesn't exist, try next
		}

		matches := secretKeyRegex.FindSubmatch(content)
		if matches != nil {
			secretKey := string(matches[1])
			filename := filepath.Base(phpFile)
			if dryRun {
				fmt.Printf("  Would extract MW_SECRET_KEY from %s to .env\n", filename)
			} else {
				if err := canasta.SaveEnvVariable(envPath, "MW_SECRET_KEY", secretKey); err != nil {
					return false, fmt.Errorf("failed to save MW_SECRET_KEY to .env: %w", err)
				}
				fmt.Printf("  Extracted MW_SECRET_KEY from %s to .env\n", filename)
			}
			return true, nil
		}
	}

	// No secret key found in any PHP file — generate a new one
	keyBytes := make([]byte, 32)
	if _, err := rand.Read(keyBytes); err != nil {
		return false, fmt.Errorf("failed to generate secret key: %w", err)
	}
	secretKey := hex.EncodeToString(keyBytes)

	if dryRun {
		fmt.Println("  Would generate new MW_SECRET_KEY in .env")
	} else {
		if err := canasta.SaveEnvVariable(envPath, "MW_SECRET_KEY", secretKey); err != nil {
			return false, fmt.Errorf("failed to save MW_SECRET_KEY to .env: %w", err)
		}
		fmt.Println("  Generated new MW_SECRET_KEY in .env")
	}

	return true, nil
}

// migrateDirectoryStructure moves legacy config directories to the new structure:
//   - config/<wiki_id>/ → config/settings/wikis/<wiki_id>/
//   - config/settings/*.php → config/settings/global/*.php
func migrateDirectoryStructure(installPath string, dryRun bool) (bool, error) {
	changed := false

	// Migrate per-wiki settings
	yamlPath := filepath.Join(installPath, "config", "wikis.yaml")
	wikiIDs, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		// No wikis.yaml means nothing to migrate
		return false, nil
	}

	for _, wikiID := range wikiIDs {
		// Normalize wikiID (same as in canasta.go)
		id := strings.Replace(wikiID, " ", "_", -1)
		id = regexp.MustCompile("[^a-zA-Z0-9_]+").ReplaceAllString(id, "")

		legacyPath := filepath.Join(installPath, "config", id)
		newPath := filepath.Join(installPath, "config", "settings", "wikis", id)

		// Check if legacy path exists and new path doesn't
		if info, err := os.Stat(legacyPath); err == nil && info.IsDir() {
			if _, err := os.Stat(newPath); os.IsNotExist(err) {
				if dryRun {
					fmt.Printf("  Would move %s -> %s\n", legacyPath, newPath)
				} else {
					fmt.Printf("  Moving %s -> %s\n", legacyPath, newPath)
					// Create parent directory
					if err := os.MkdirAll(filepath.Dir(newPath), os.ModePerm); err != nil {
						return false, fmt.Errorf("failed to create directory: %w", err)
					}
					// Move directory
					if err := os.Rename(legacyPath, newPath); err != nil {
						return false, fmt.Errorf("failed to move %s: %w", legacyPath, err)
					}
				}
				changed = true
			}
		}
	}

	// Migrate global settings
	settingsPath := filepath.Join(installPath, "config", "settings")
	globalPath := filepath.Join(settingsPath, "global")

	entries, err := os.ReadDir(settingsPath)
	if err == nil {
		for _, entry := range entries {
			if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".php") {
				legacyFile := filepath.Join(settingsPath, entry.Name())
				newFile := filepath.Join(globalPath, entry.Name())

				// Check if file doesn't exist in global/
				if _, err := os.Stat(newFile); os.IsNotExist(err) {
					if dryRun {
						fmt.Printf("  Would move %s -> %s\n", legacyFile, newFile)
					} else {
						fmt.Printf("  Moving %s -> %s\n", legacyFile, newFile)
						// Create global directory if needed
						if err := os.MkdirAll(globalPath, os.ModePerm); err != nil {
							return false, fmt.Errorf("failed to create directory: %w", err)
						}
						// Move file
						if err := os.Rename(legacyFile, newFile); err != nil {
							return false, fmt.Errorf("failed to move %s: %w", legacyFile, err)
						}
					}
					changed = true
				}
			}
		}
	}

	return changed, nil
}

// fixVectorDefaultSkin ensures Vector.php includes $wgDefaultSkin if it exists
func fixVectorDefaultSkin(installPath string, dryRun bool) (bool, error) {
	// Check both names: legacy "Vector.php" and current "30-Vector.php" (after git pull)
	vectorPath := filepath.Join(installPath, "config", "settings", "global", "Vector.php")
	if _, err := os.Stat(vectorPath); err != nil {
		vectorPath = filepath.Join(installPath, "config", "settings", "global", "30-Vector.php")
		if _, err := os.Stat(vectorPath); err != nil {
			return false, nil // Neither file exists, nothing to fix
		}
	}

	content, err := os.ReadFile(vectorPath)
	if err != nil {
		return false, fmt.Errorf("failed to read Vector.php: %w", err)
	}

	if strings.Contains(string(content), "wgDefaultSkin") {
		return false, nil // Already has $wgDefaultSkin
	}

	if dryRun {
		fmt.Println("  Would add $wgDefaultSkin to Vector.php")
	} else {
		fmt.Println("  Adding $wgDefaultSkin to Vector.php")
		newContent := strings.Replace(
			string(content),
			"wfLoadSkin( 'Vector' );",
			"$wgDefaultSkin = \"vector-2022\";\nwfLoadSkin( 'Vector' );",
			1,
		)
		if err := os.WriteFile(vectorPath, []byte(newContent), 0644); err != nil {
			return false, fmt.Errorf("failed to update Vector.php: %w", err)
		}
	}

	return true, nil
}
