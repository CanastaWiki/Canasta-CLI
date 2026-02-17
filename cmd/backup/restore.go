package backup

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

func restoreCmdCreate() *cobra.Command {

	var (
		snapshotId         string
		skipBeforeSnapshot bool
	)

	restoreCmd := &cobra.Command{
		Use:   "restore",
		Short: "Restore a backup",
		Long: `Restore a Canasta installation from a backup snapshot. By default, a safety
snapshot is taken before restoring. The restore replaces configuration files,
extensions, images, skins, public_assets, .env, docker-compose.override.yml,
my.cnf, and all wiki databases with the contents of the specified snapshot.`,
		Example: `  # Restore a snapshot by ID
  canasta backup restore -i myinstance -s abc123

  # Restore without taking a safety snapshot first
  canasta backup restore -i myinstance -s abc123 --skip-safety-backup`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return restoreSnapshot(snapshotId, skipBeforeSnapshot)
		},
	}

	restoreCmd.Flags().StringVarP(&snapshotId, "snapshot", "s", "", "Snapshot ID (required)")
	restoreCmd.Flags().BoolVar(&skipBeforeSnapshot, "skip-safety-backup", false, "Skip taking a safety backup before restore")
	_ = restoreCmd.MarkFlagRequired("snapshot")
	return restoreCmd
}

func restoreSnapshot(snapshotId string, skipBeforeSnapshot bool) error {
	EnvVariables, envErr := canasta.GetEnvVariable(envPath)
	if envErr != nil {
		return envErr
	}

	if !skipBeforeSnapshot {
		logging.Print("Taking snapshot...")
		if err := takeSnapshot("BeforeRestoring-" + snapshotId); err != nil {
			return err
		}
		logging.Print("Snapshot taken...")
	}

	logging.Print("Restoring snapshot to backup volume...")
	_, err := runBackup(nil, "-r", repoURL, "restore", snapshotId, "--target", "/")
	if err != nil {
		return err
	}

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

	logging.Print("Restoring database...")
	wikiIDs, err := getWikiIDsForRestore(instance.Path)
	if err != nil {
		return err
	}
	for _, id := range wikiIDs {
		logging.Print(fmt.Sprintf("Restoring database for wiki '%s'...", id))
		command := fmt.Sprintf("mysql -h db -u root -p%s < %s",
			EnvVariables["MYSQL_PASSWORD"], dumpPath(id))
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
