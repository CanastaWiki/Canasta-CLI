package backup

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
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
extensions, images, skins, and the database with the contents of the
specified snapshot.`,
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
	currentSnapshotFolder := instance.Path + "/currentsnapshot"

	if !skipBeforeSnapshot {
		logging.Print("Taking snapshot...")
		if err := takeSnapshot("BeforeRestoring-" + snapshotId); err != nil {
			return err
		}
		logging.Print("Snapshot taken...")
	}

	if err := checkCurrentSnapshotFolder(currentSnapshotFolder); err != nil {
		return err
	}

	logging.Print("Restoring snapshot to /currentsnapshot")
	volumes := map[string]string{
		currentSnapshotFolder: "/currentsnapshot",
	}
	_, err := runBackup(volumes, "-r", repoURL, "restore", snapshotId, "--target", "/currentsnapshot")
	if err != nil {
		return err
	}

	logging.Print("Copying files....")
	for _, dir := range []string{"config", "extensions", "images", "skins"} {
		dst := filepath.Join(instance.Path, dir)
		if err := os.RemoveAll(dst); err != nil {
			logging.Print(err.Error())
		}
		src := filepath.Join(currentSnapshotFolder, "currentsnapshot", dir)
		if err := copyDir(src, dst); err != nil {
			logging.Print(fmt.Sprintf("failed to copy %s: %s", dir, err.Error()))
		}
	}
	logging.Print("Copied files...")

	logging.Print("Restoring database...")
	command := fmt.Sprintf("mysql -h db -u root -p%s %s < /mediawiki/config/db.sql", EnvVariables["MYSQL_PASSWORD"], EnvVariables["WG_DB_NAME"])
	_, restoreErr := orch.ExecWithError(instance.Path, "web", command)
	if restoreErr != nil {
		return fmt.Errorf("database restore failed: %w", restoreErr)
	}
	logging.Print("Restored database...")
	return nil
}
