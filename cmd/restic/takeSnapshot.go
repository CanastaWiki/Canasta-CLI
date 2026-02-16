package restic

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
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
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return takeSnapshot(tag)
		},
	}

	takeSnapshotCmd.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot tag (required)")
	return takeSnapshotCmd
}

func takeSnapshot(tag string) error {
	fmt.Printf("Taking snapshot '%s'...\n", tag)
	EnvVariables, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return err
	}
	currentSnapshotFolder := instance.Path + "/currentsnapshot"

	if err := checkCurrentSnapshotFolder(currentSnapshotFolder); err != nil {
		return err
	}

	_, err = orch.ExecWithError(instance.Path, "web", fmt.Sprintf("mysqldump -h db -u root -p%s --databases %s > %s", EnvVariables["MYSQL_PASSWORD"], EnvVariables["WG_DB_NAME"], mysqldumpPath))
	if err != nil {
		return fmt.Errorf("mysqldump failed: %w", err)
	}
	logging.Print("mysqldump mediawiki completed")

	err, output := execute.Run(instance.Path, "sudo", "cp", "--preserve=links,mode,ownership,timestamps", "-r", "config", "extensions", "images", "skins", currentSnapshotFolder)
	if err != nil {
		return fmt.Errorf("%s", output)
	}
	logging.Print("Copy folders and files completed.")

	hostname, _ := os.Hostname()
	volumes := map[string]string{
		currentSnapshotFolder: "/currentsnapshot/",
	}
	output, err = runRestic(volumes, "-r", repoURL, "--tag", fmt.Sprintf("%s__on__%s", tag, hostname), "backup", "/currentsnapshot")
	if err != nil {
		return err
	}
	fmt.Print(output)
	fmt.Println("Completed running restic backup")
	return nil
}
