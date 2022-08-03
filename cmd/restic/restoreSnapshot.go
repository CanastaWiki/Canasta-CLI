package restic

import (
	"fmt"
	"os"
	"os/exec"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

func restoreSnapshotCmdCreate() *cobra.Command {

	var (
		snapshotId         string
		skipBeforeSnapshot bool
	)

	restoreSnapshotCmd := &cobra.Command{
		Use:   "restore",
		Short: "Restore restic snapshot",
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
	command := fmt.Sprintf("docker run --rm -i --env-file %s/.env -v %s:/currentsnapshot restic/restic -r s3:%s/%s restore %s --target /currentsnapshot", instance.Path, currentSnapshotFolder, EnvVariables["AWS_S3_API"], EnvVariables["AWS_S3_BUCKET"], snapshotId)
	commandArgs := strings.Fields(command)
	execute.Run("", "sudo", commandArgs...)

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
	command = fmt.Sprintf("mysql -h db -u root -p%s %s < /mediawiki/config/db.sql", EnvVariables["MYSQL_PASSWORD"], EnvVariables["WG_DB_NAME"])
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", command)
	logging.Print("Restored database...")
}
