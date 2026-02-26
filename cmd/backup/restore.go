package backup

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newRestoreCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {

	var (
		snapshotId         string
		skipBeforeSnapshot bool
		wikiID             string
	)

	restoreCmd := &cobra.Command{
		Use:   "restore",
		Short: "Restore a backup",
		Long: `Restore a Canasta installation from a backup snapshot. By default, a safety
snapshot is taken before restoring. The restore replaces configuration files,
extensions, images, skins, public_assets, .env, docker-compose.override.yml,
my.cnf, and all wiki databases with the contents of the specified snapshot.

Use -w/--wiki to restore only a single wiki's database, per-wiki settings,
images, and public assets from the backup, leaving shared files untouched.`,
		Example: `  # Restore a snapshot by ID
  canasta backup restore -i myinstance -s abc123

  # Restore without taking a safety snapshot first
  canasta backup restore -i myinstance -s abc123 --skip-safety-backup

  # Restore only a single wiki's database
  canasta backup restore -i myinstance -s abc123 -w wiki2`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return restoreSnapshot(*orch, *instance, *envPath, *repoURL, snapshotId, skipBeforeSnapshot, wikiID)
		},
	}

	restoreCmd.Flags().StringVarP(&snapshotId, "snapshot", "s", "", "Snapshot ID (required)")
	restoreCmd.Flags().BoolVar(&skipBeforeSnapshot, "skip-safety-backup", false, "Skip taking a safety backup before restore")
	restoreCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "Restore only this wiki's database and per-wiki files")
	_ = restoreCmd.MarkFlagRequired("snapshot")
	return restoreCmd
}

func restoreSnapshot(orch orchestrators.Orchestrator, instance config.Installation, envPath, repoURL, snapshotId string, skipBeforeSnapshot bool, wikiID string) error {
	EnvVariables, envErr := canasta.GetEnvVariable(envPath)
	if envErr != nil {
		return envErr
	}

	if !skipBeforeSnapshot {
		logging.Print("Taking snapshot...")
		if err := takeSnapshot(orch, instance, envPath, repoURL, "BeforeRestoring-"+snapshotId); err != nil {
			return err
		}
		logging.Print("Snapshot taken...")
	}

	logging.Print("Restoring snapshot to backup volume...")
	_, err := runBackup(orch, instance.Path, envPath, nil, "-r", repoURL, "restore", snapshotId, "--target", "/")
	if err != nil {
		return err
	}

	if wikiID != "" {
		return restoreWiki(orch, instance, wikiID, EnvVariables)
	}
	return restoreFull(orch, instance, EnvVariables)
}

// restoreWiki restores a single wiki's database, per-wiki settings, images,
// and public assets from a backup, leaving shared files untouched.
func restoreWiki(orch orchestrators.Orchestrator, instance config.Installation, wikiID string, env map[string]string) error {
	// Validate that the wiki ID exists in the current installation's wikis.yaml
	wikiIDs, err := getWikiIDs(instance.Path)
	if err != nil {
		return err
	}
	found := false
	for _, id := range wikiIDs {
		if id == wikiID {
			found = true
			break
		}
	}
	if !found {
		return fmt.Errorf("wiki '%s' not found in current installation's wikis.yaml", wikiID)
	}

	// Copy per-wiki files from the backup volume:
	// - database dump from config/backup/
	// - per-wiki settings from config/settings/wikis/{wikiID}/
	// - per-wiki images from images/{wikiID}/
	// - per-wiki public assets from public_assets/{wikiID}/
	logging.Print("Copying per-wiki files from backup volume...")
	paths := map[string]string{
		"/currentsnapshot/config/backup": filepath.Join(instance.Path, "config", "backup"),
		fmt.Sprintf("/currentsnapshot/config/settings/wikis/%s", wikiID): filepath.Join(instance.Path, "config", "settings", "wikis", wikiID),
		fmt.Sprintf("/currentsnapshot/images/%s", wikiID):                filepath.Join(instance.Path, "images", wikiID),
		fmt.Sprintf("/currentsnapshot/public_assets/%s", wikiID):         filepath.Join(instance.Path, "public_assets", wikiID),
	}
	if err := orch.RestoreFromBackupVolume(instance.Path, paths); err != nil {
		return err
	}

	// Validate that the dump file exists
	hostDumpPath := filepath.Join(instance.Path, "config", "backup", fmt.Sprintf("db_%s.sql", wikiID))
	if _, err := os.Stat(hostDumpPath); err != nil {
		os.RemoveAll(filepath.Join(instance.Path, "config", "backup"))
		return fmt.Errorf("database dump file for wiki '%s' not found in backup snapshot", wikiID)
	}

	// Import the database
	logging.Print(fmt.Sprintf("Restoring database for wiki '%s'...", wikiID))
	command := fmt.Sprintf("mysql -h db -u root -p%s < %s",
		env["MYSQL_PASSWORD"], dumpPath(wikiID))
	_, restoreErr := orch.ExecWithError(instance.Path, "web", command)
	if restoreErr != nil {
		os.RemoveAll(filepath.Join(instance.Path, "config", "backup"))
		return fmt.Errorf("Database restore failed for wiki '%s': %w", wikiID, restoreErr)
	}

	// Clean up the database dump directory
	os.RemoveAll(filepath.Join(instance.Path, "config", "backup"))

	logging.Print("Per-wiki restore completed")
	fmt.Printf("Restore completed for wiki '%s'\n", wikiID)
	return nil
}

// restoreFull performs a full restore of all files and databases from a backup.
func restoreFull(orch orchestrators.Orchestrator, instance config.Installation, env map[string]string) error {
	logging.Print("Copying files from backup volume...")
	paths := make(map[string]string)
	for _, dir := range []string{"config", "extensions", "images", "skins", "public_assets"} {
		paths["/currentsnapshot/"+dir] = filepath.Join(instance.Path, dir)
	}
	for _, file := range []string{".env", "docker-compose.override.yml", "my.cnf"} {
		paths["/currentsnapshot/"+file] = filepath.Join(instance.Path, file)
	}
	if err := orch.RestoreFromBackupVolume(instance.Path, paths); err != nil {
		return err
	}
	logging.Print("Copied files...")

	logging.Print("Preserving database passwords...")
	for _, key := range []string{"MYSQL_PASSWORD", "WIKI_DB_PASSWORD"} {
		if val, ok := env[key]; ok {
			if err := canasta.SaveEnvVariable(filepath.Join(instance.Path, ".env"), key, val); err != nil {
				return fmt.Errorf("failed to preserve %s in .env: %w", key, err)
			}
		}
	}

	logging.Print("Restoring database...")
	wikiIDs, err := getWikiIDsForRestore(instance.Path)
	if err != nil {
		return err
	}
	for _, id := range wikiIDs {
		logging.Print(fmt.Sprintf("Restoring database for wiki '%s'...", id))
		command := fmt.Sprintf("mysql -h db -u root -p%s < %s",
			env["MYSQL_PASSWORD"], dumpPath(id))
		_, restoreErr := orch.ExecWithError(instance.Path, "web", command)
		if restoreErr != nil {
			return fmt.Errorf("Database restore failed for wiki '%s': %w", id, restoreErr)
		}
	}

	// Clean up the backup directory containing database dumps
	os.RemoveAll(filepath.Join(instance.Path, "config", "backup"))

	logging.Print("Database restore completed")
	fmt.Println("Restore completed")
	return nil
}

// getWikiIDsForRestore determines which wiki databases have per-wiki dump files
// in the restored config directory.
func getWikiIDsForRestore(installPath string) ([]string, error) {
	yamlPath := filepath.Join(installPath, "config", "wikis.yaml")
	ids, _, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return nil, fmt.Errorf("Failed to read wikis.yaml: %w", err)
	}

	var result []string
	for _, id := range ids {
		hostDumpPath := filepath.Join(installPath, "config", "backup", fmt.Sprintf("db_%s.sql", id))
		if _, statErr := os.Stat(hostDumpPath); statErr == nil {
			result = append(result, id)
		}
	}

	if len(result) == 0 {
		return nil, fmt.Errorf("No database dump files found in backup")
	}
	return result, nil
}
