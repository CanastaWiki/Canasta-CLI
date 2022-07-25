package restic

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/spf13/cobra"
)

func forgetSnapshotCmdCreate() *cobra.Command {

	forgetSnapshotCmd := &cobra.Command{
		Use:   "forget",
		Short: "Forget restic snapshots",
		RunE: func(cmd *cobra.Command, args []string) error {
			if tag == "" && args[0] == "" {
				return fmt.Errorf("You must provide a restic snapshot tag")
			} else if tag == "" {
				tag = args[0]
			}
			forgetSnapshot()
			return nil
		},
	}

	forgetSnapshotCmd.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot ID (required)")
	return forgetSnapshotCmd
}

func forgetSnapshot() {
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)

	output := execute.Run(instance.Path, "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "restic/restic", "-r", "s3:"+EnvVariables["AWS_S3_API"]+"/"+EnvVariables["AWS_S3_BUCKET"], "forget", tag)
	fmt.Print(output)
}
