package upgrade

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/perms"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/imagebuild"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/selfupdate"
)

var dryRun bool

func NewCmdCreate() *cobra.Command {
	var upgradeCmd = &cobra.Command{
		Use:   "upgrade",
		Short: "Upgrade the Canasta CLI and all registered installations",
		Long: `Upgrade the Canasta CLI binary and all registered installations.

Each installation is updated by refreshing configuration files, pulling the
latest container images, running any necessary migrations, and restarting
the containers.

The CLI itself is also updated to the latest version before upgrading instances.
Dev builds (compiled without version ldflags) skip the CLI self-update automatically.

If an installation fails to upgrade, the error is printed and the remaining
installations are still upgraded. A summary at the end reports how many succeeded.

Use --dry-run to preview migrations without applying them.

Installations created with --build-from automatically rebuild the Canasta image
from the stored source path during upgrade. For Kubernetes installations created
with a kind cluster or custom registry, the rebuilt image is automatically
distributed using the stored configuration.`,
		Example: `  # Upgrade the CLI and all installations
  canasta upgrade

  # Preview what would change without applying
  canasta upgrade --dry-run`,
		RunE: func(cmd *cobra.Command, args []string) error {
			// Check for CLI updates first (skipped automatically for dev builds and dry-run)
			if !dryRun {
				if _, err := selfupdate.CheckAndUpdate(); err != nil {
					return fmt.Errorf("CLI update failed: %w", err)
				}
				fmt.Println()
			}

			return upgradeAllInstances(dryRun)
		},
	}
	upgradeCmd.Flags().BoolVar(&dryRun, "dry-run", false, "Show what would change without applying")
	return upgradeCmd
}

func upgradeAllInstances(dryRun bool) error {
	installations, err := config.GetAll()
	if err != nil {
		return err
	}
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

	orch, err := orchestrators.NewFromInstance(instance)
	if err != nil {
		return err
	}

	if dryRun {
		fmt.Println("Dry run mode - showing what would change without applying")
	}

	fmt.Println("Checking for configuration file updates...")
	stackChanged, err := orch.UpdateStackFiles(instance.Path, dryRun)
	if err != nil {
		return err
	}

	// Update CLI-managed template files (READMEs, new files added in this CLI version).
	// User-editable files are only created if missing.
	if !dryRun {
		if err := canasta.UpdateInstallationTemplate(instance.Path); err != nil {
			return err
		}
	}

	// Update container images
	var imagesUpdated bool
	envPath := filepath.Join(instance.Path, ".env")

	if !dryRun {
		if instance.BuildFrom != "" {
			// Rebuild Canasta image from stored source path
			fmt.Printf("Rebuilding Canasta image from %s...\n", instance.BuildFrom)
			imageTag, err := imagebuild.BuildFromSource(instance.BuildFrom)
			if err != nil {
				return fmt.Errorf("failed to build from source: %w", err)
			}

			// Distribute the rebuilt image based on stored config
			if instance.KindCluster != "" {
				fmt.Println("Loading rebuilt image into kind cluster...")
				if err := orchestrators.LoadImageToKind(instance.KindCluster, imageTag); err != nil {
					return fmt.Errorf("failed to load image into kind: %w", err)
				}
			} else if instance.Registry != "" {
				fmt.Println("Pushing rebuilt image to registry...")
				remoteTag, err := imagebuild.PushImage(imageTag, instance.Registry)
				if err != nil {
					return fmt.Errorf("failed to push image to registry: %w", err)
				}
				if err := canasta.SaveEnvVariable(envPath, "CANASTA_IMAGE", remoteTag); err != nil {
					return err
				}
			}

			// For K8s, regenerate kustomization so it picks up the image
			if !orch.SupportsImagePull() {
				if err := orch.UpdateConfig(instance.Path); err != nil {
					return err
				}
			}

			imagesUpdated = true
		} else if !orch.SupportsImagePull() {
			fmt.Println("Regenerating configuration and re-applying manifests...")
			if err := orch.UpdateConfig(instance.Path); err != nil {
				return err
			}
			imagesUpdated = true
		} else {
			fmt.Println("Pulling Canasta container images...")
			report, err := orch.Update(instance.Path)
			if err != nil {
				return err
			}
			if len(report.UpdatedImages) > 0 {
				imagesUpdated = true
				fmt.Println("Container images updated:")
				for _, img := range report.UpdatedImages {
					fmt.Printf("  %s (%s)\n", img.Service, img.Image)
				}
			} else {
				fmt.Println("Container images are up to date.")
			}
		}
	}

	// Run migration steps (before restart so config is correct when containers come up)
	migrationsNeeded, err := runMigration(instance.Path, orch, dryRun)
	if err != nil {
		return err
	}

	if dryRun {
		fmt.Println()
		if stackChanged || migrationsNeeded {
			fmt.Println("Run 'canasta upgrade' to apply these changes.")
		} else {
			fmt.Println("Installation is up to date. No upgrade needed.")
		}
		return nil
	}

	// Only restart if something changed
	if stackChanged || migrationsNeeded || imagesUpdated {
		// Restart the containers
		fmt.Println("Restarting containers...")
		if err = orch.Stop(instance); err != nil {
			return err
		}
		if err = orch.Start(instance); err != nil {
			return err
		}

		// Touch LocalSettings.php to flush cache
		fmt.Print("Running 'touch LocalSettings.php' to flush cache\n")
		_, err = orch.ExecWithError(instance.Path, "web", "touch LocalSettings.php")
		if err != nil {
			return err
		}

		fmt.Println()
		fmt.Println("Canasta upgraded successfully!")
	} else {
		fmt.Println()
		fmt.Println("Installation is already up to date.")
	}

	return nil
}

// runMigration orchestrates all migration steps
func runMigration(installPath string, orch orchestrators.Orchestrator, dryRun bool) (bool, error) {
	fmt.Println("Checking for config migrations...")

	changed := false

	// Step 1: Extract or generate MW_SECRET_KEY
	keyChanged, err := extractSecretKey(installPath, dryRun)
	if err != nil {
		return false, err
	}
	if keyChanged {
		changed = true
	}

	// Step 2: Migrate directory structure
	dirChanged, err := migrateDirectoryStructure(installPath, dryRun)
	if err != nil {
		return false, err
	}
	if dirChanged {
		changed = true
	}

	// Step 3: Fix Vector.php default skin
	vectorChanged, err := fixVectorDefaultSkin(installPath, dryRun)
	if err != nil {
		return false, err
	}
	if vectorChanged {
		changed = true
	}

	// Step 4: Orchestrator-specific config migrations (Caddyfiles, etc.)
	orchChanged, err := orch.MigrateConfig(installPath, dryRun)
	if err != nil {
		return false, err
	}
	if orchChanged {
		changed = true
	}

	// Step 5: Remove empty composer.local.json so the image's version is synced
	composerChanged, err := removeEmptyComposerLocal(installPath, dryRun)
	if err != nil {
		return false, err
	}
	if composerChanged {
		changed = true
	}

	// Step 6: Remove legacy .git directory (installations are no longer git repos)
	gitChanged, err := removeLegacyGitDir(installPath, dryRun)
	if err != nil {
		return false, err
	}
	if gitChanged {
		changed = true
	}

	if !changed {
		fmt.Println("No config migrations needed.")
	} else if dryRun {
		fmt.Println("Config migrations would be applied.")
	} else {
		fmt.Println("Migrations applied.")
	}

	return changed, nil
}

// extractSecretKey extracts $wgSecretKey from PHP config files into .env as MW_SECRET_KEY,
// or generates a new one if not found
func extractSecretKey(installPath string, dryRun bool) (bool, error) {
	envPath := filepath.Join(installPath, ".env")

	// Check if MW_SECRET_KEY is already set in .env
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return false, err
	}
	if val, ok := envVars["MW_SECRET_KEY"]; ok && val != "" {
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
			id := strings.ReplaceAll(wikiID, " ", "_")
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
		id := strings.ReplaceAll(wikiID, " ", "_")
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
					if err := os.MkdirAll(filepath.Dir(newPath), perms.DirPerm); err != nil {
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
						if err := os.MkdirAll(globalPath, perms.DirPerm); err != nil {
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

// removeEmptyComposerLocal removes config/composer.local.json if it exists with
// an empty include array. This allows the image's build-time version (with specific
// bundled extension entries) to be synced via rsync on the next container recreate.
func removeEmptyComposerLocal(installPath string, dryRun bool) (bool, error) {
	filePath := filepath.Join(installPath, "config", "composer.local.json")

	content, err := os.ReadFile(filePath)
	if err != nil {
		return false, nil // File doesn't exist, nothing to do
	}

	var data struct {
		Extra struct {
			MergePlugin struct {
				Include []string `json:"include"`
			} `json:"merge-plugin"`
		} `json:"extra"`
	}
	if err := json.Unmarshal(content, &data); err != nil {
		// Can't parse — leave it alone
		return false, nil
	}

	if len(data.Extra.MergePlugin.Include) > 0 {
		return false, nil // Non-empty includes, leave it alone
	}

	if dryRun {
		fmt.Println("  Would remove empty config/composer.local.json (image will sync its version on next recreate)")
	} else {
		fmt.Println("  Removing empty config/composer.local.json (image will sync its version on next recreate)")
		if err := os.Remove(filePath); err != nil {
			return false, fmt.Errorf("failed to remove composer.local.json: %w", err)
		}
	}

	return true, nil
}

// fixVectorDefaultSkin ensures Vector.php includes $wgDefaultSkin if it exists
func fixVectorDefaultSkin(installPath string, dryRun bool) (bool, error) {
	vectorPath := filepath.Join(installPath, "config", "settings", "global", "Vector.php")
	if _, err := os.Stat(vectorPath); err != nil {
		return false, nil // File doesn't exist, nothing to fix
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
		if err := os.WriteFile(vectorPath, []byte(newContent), perms.FilePerm); err != nil {
			return false, fmt.Errorf("failed to update Vector.php: %w", err)
		}
	}

	return true, nil
}

// removeLegacyGitDir removes the .git directory from installations that were
// previously cloned from the Canasta-DockerCompose repo. Stack files are now
// embedded in the CLI binary, so installations are no longer git repos.
func removeLegacyGitDir(installPath string, dryRun bool) (bool, error) {
	gitDir := filepath.Join(installPath, ".git")
	if _, err := os.Stat(gitDir); os.IsNotExist(err) {
		return false, nil
	} else if err != nil {
		return false, err
	}

	if dryRun {
		fmt.Println("  Would remove legacy .git directory")
	} else {
		fmt.Println("  Removing legacy .git directory")
		if err := os.RemoveAll(gitDir); err != nil {
			return false, fmt.Errorf("failed to remove .git directory: %w", err)
		}
	}

	return true, nil
}
