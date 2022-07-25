package restic

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

func takeSnapshotCmdCreate() *cobra.Command {

	takeSnapshotCmd := &cobra.Command{
		Use:   "take-snapshot",
		Short: "Take restic snapshots",
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
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)
	currentSnapshotFolder := instance.Path + "/currentsnapshot"

	checkCurrentSnapshotFolder(currentSnapshotFolder)

	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", fmt.Sprintf("mysqldump -h db -u root -p%s --databases %s > %s", EnvVariables["MYSQL_PASSWORD"], EnvVariables["WG_DB_NAME"], mysqldumpPath))
	logging.Print("mysqldump mediawiki completed")

	execute.Run(instance.Path, "sudo", "cp", "--preserve=links,mode,ownership,timestamps", "-r", "config", "extensions", "images", "skins", currentSnapshotFolder)
	logging.Print("Copy folders and files completed.")

	hostname, _ := os.Hostname()
	output := execute.Run(instance.Path, "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "-v", currentSnapshotFolder+":/currentsnapshot/", "restic/restic", "-r", "s3:"+EnvVariables["AWS_S3_API"]+"/"+EnvVariables["AWS_S3_BUCKET"], "--tag", fmt.Sprintf("%s__on__%s", tag, hostname), "backup", "/currentsnapshot")
	fmt.Print(output)
	fmt.Println("Completed running restic backup")
}
