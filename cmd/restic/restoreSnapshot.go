package restic

import (
	"fmt"
	"os"
	"os/exec"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func restoreSnapshotCmdCreate() *cobra.Command {

	var (
		snapshotId         string
		skipBeforeSnapshot bool
	)

	restoreSnapshotCmd := &cobra.Command{
		Use:   "restore",
		Short: "Restore restic snapshot",
		Long: `Restore a Canasta installation from a Restic snapshot. By default, a safety
snapshot is taken before restoring. The restore replaces configuration files,
extensions, images, skins, and the database with the contents of the
specified snapshot.`,
		Example: `  # Restore a snapshot by ID
  canasta restic restore -i myinstance -s abc123

  # Restore without taking a safety snapshot first
  canasta restic restore -i myinstance -s abc123 -r`,
		RunE: func(cmd *cobra.Command, args []string) error {

			restoreSnapshot(snapshotId, skipBeforeSnapshot)
			return nil
		},
	}

	restoreSnapshotCmd.Flags().StringVarP(&snapshotId, "snapshot-id", "s", "", "Restic snapshot ID (required)")
	restoreSnapshotCmd.Flags().BoolVarP(&skipBeforeSnapshot, "skip-before-restore-snapshot", "r", false, "Skips taking snapshot before restore")
	restoreSnapshotCmd.MarkFlagRequired("snapshot-id")
	return restoreSnapshotCmd
}

func restoreSnapshot(snapshotId string, skipBeforeSnapshot bool) {
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)
	currentSnapshotFolder := instance.Path + "/currentsnapshot"

	if !skipBeforeSnapshot {
		logging.Print("Taking snapshot...")
		takeSnapshot("BeforeRestoring-" + snapshotId)
		logging.Print("Snapshot taken...")
	}

	checkCurrentSnapshotFolder(currentSnapshotFolder)

	logging.Print("Restoring snapshot to /currentsnapshot")
	repoURL := getRepoURL(EnvVariables)
	command := fmt.Sprintf("docker run --rm -i --env-file %s/.env -v %s:/currentsnapshot restic/restic -r %s restore %s --target /currentsnapshot", instance.Path, currentSnapshotFolder, repoURL, snapshotId)
	commandArgs := strings.Fields(command)
	err, output := execute.Run("", "sudo", commandArgs...)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	}
	logging.Print("Copying files....")
	folders := [...]string{"/config", "/extensions", "/images", "/skins"}
	for _, folder := range folders {
		if err := os.RemoveAll(currentSnapshotFolder + folder); err != nil {
			logging.Print(err.Error())
		}
		output, err := exec.Command("sudo", "cp", "-r", "--preserve=links,mode,ownership,timestamps", fmt.Sprintf("%s/currentsnapshot%s/", currentSnapshotFolder, folder), instance.Path).CombinedOutput()
		if err != nil {
			logging.Print(err.Error())
			logging.Print(string(output))
		}
	}
	logging.Print("Copied files...")

	logging.Print("Restoring database...")
	orch := orchestrators.New(instance.Orchestrator)
	command = fmt.Sprintf("mysql -h db -u root -p%s %s < /mediawiki/config/db.sql", EnvVariables["MYSQL_PASSWORD"], EnvVariables["WG_DB_NAME"])
	_, restoreErr := orch.ExecWithError(instance.Path, "web", command)
	if restoreErr != nil {
		logging.Fatal(fmt.Errorf("database restore failed: %w", restoreErr))
	}
	logging.Print("Restored database...")
}
