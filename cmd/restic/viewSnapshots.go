package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
)

func viewSnapshotsCmdCreate() *cobra.Command {

	initCmd := &cobra.Command{
		Use:   "view",
		Short: "View restic snapshots",
		RunE: func(cmd *cobra.Command, args []string) error {
			viewSnapshots()
			return nil
		},
	}
	return initCmd
}

func viewSnapshots() {
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)

	output := execute.Run(instance.Path, "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "restic/restic", "-r", "s3:"+EnvVariables["AWS_S3_API"]+"/"+EnvVariables["AWS_S3_BUCKET"], "snapshots")
	fmt.Print(output)
}
