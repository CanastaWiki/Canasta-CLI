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

var (
	tag      string
	hostname string
)

func takeSnapshotCmdCreate() *cobra.Command {

	takeSnapshotCmdCreate := &cobra.Command{
		Use:   "take-snapshot",
		Short: "Take restic snapshots",
		RunE: func(cmd *cobra.Command, args []string) error {
			takeSnapshot()
			return nil
		},
	}

	takeSnapshotCmdCreate.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot tag (required)")
	takeSnapshotCmdCreate.MarkFlagRequired("tag")
	return takeSnapshotCmdCreate
}

func takeSnapshot() {
	fmt.Printf("Taking snapshot '%s'...\n", tag)
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)
	currentSnapshotFolder := instance.Path + "/currentsnapshot"

	if _, err := os.Stat(currentSnapshotFolder); err != nil {
		if os.IsNotExist(err) {
			logging.Print("Creating..." + currentSnapshotFolder)
			if err := os.Mkdir(currentSnapshotFolder, os.ModePerm); err != nil {
				logging.Fatal(err)
			}
		} else {
			logging.Fatal(err)
		}
	} else {
		logging.Print("Emptying... " + currentSnapshotFolder)
		if err := os.RemoveAll(currentSnapshotFolder); err != nil {
			logging.Fatal(err)
		}
		if err := os.Mkdir(currentSnapshotFolder, os.ModePerm); err != nil {
			logging.Fatal(err)
		}
		logging.Print("Emptied.. " + currentSnapshotFolder)
	}

	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", fmt.Sprintf("mysqldump -h db -u root -p%s --databases %s > %s", EnvVariables["MYSQL_PASSWORD"], EnvVariables["WG_DB_NAME"], mysqldumpPath))
	logging.Print("mysqldump mediawiki completed")

	execute.Run(instance.Path, "sudo", "cp", "--preserve=links,mode,ownership,timestamps", "-r", "config", "extensions", "images", "skins", currentSnapshotFolder)
	logging.Print("Copy folders and files completed.")

	hostname, _ = os.Hostname()
	output := execute.Run(instance.Path, "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "-v", currentSnapshotFolder+":/currentsnapshot/", "restic/restic", "-r", "s3:"+EnvVariables["AWS_S3_API"]+"/"+EnvVariables["AWS_S3_BUCKET"], "--tag", fmt.Sprintf("%s__on__%s", tag, hostname), "backup", "/currentsnapshot")
	fmt.Print(output)
	fmt.Println("Completed running restic backup")
}
