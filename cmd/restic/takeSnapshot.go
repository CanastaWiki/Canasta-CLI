package restic

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func takeSnapshotCmdCreate() *cobra.Command {

	takeSnapshotCmd := &cobra.Command{
		Use:   "take-snapshot",
		Short: "Take restic snapshots",
		Long: `Take a new backup snapshot of the Canasta installation. This dumps the
database, copies configuration files, extensions, images, and skins into
a staging directory, then uploads the snapshot to the Restic repository
with the specified tag.`,
		Example: `  # Take a snapshot with a descriptive tag
  canasta restic take-snapshot -i myinstance -t before-upgrade`,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			takeSnapshot(tag)
			return nil
		},
	}

	takeSnapshotCmd.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot tag (required)")
	return takeSnapshotCmd
}

func takeSnapshot(tag string) {
	fmt.Printf("Taking snapshot '%s'...\n", tag)
	orch := orchestrators.New(instance.Orchestrator)
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)
	currentSnapshotFolder := instance.Path + "/currentsnapshot"

	checkCurrentSnapshotFolder(currentSnapshotFolder)

	_, err := orch.ExecWithError(instance.Path, "web", fmt.Sprintf("mysqldump -h db -u root -p%s --databases %s > %s", EnvVariables["MYSQL_PASSWORD"], EnvVariables["WG_DB_NAME"], mysqldumpPath))
	if err != nil {
		logging.Fatal(fmt.Errorf("mysqldump failed: %w", err))
	}
	logging.Print("mysqldump mediawiki completed")

	err, output := execute.Run(instance.Path, "sudo", "cp", "--preserve=links,mode,ownership,timestamps", "-r", "config", "extensions", "images", "skins", currentSnapshotFolder)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	}
	logging.Print("Copy folders and files completed.")

	hostname, _ := os.Hostname()
	repoURL := getRepoURL(EnvVariables)
	err, output = execute.Run(instance.Path, "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "-v", currentSnapshotFolder+":/currentsnapshot/", "restic/restic", "-r", repoURL, "--tag", fmt.Sprintf("%s__on__%s", tag, hostname), "backup", "/currentsnapshot")
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	}
	fmt.Print(output)
	fmt.Println("Completed running restic backup")
}
