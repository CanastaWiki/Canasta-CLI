package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
)

func initCmdCreate() *cobra.Command {

	initCmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize a restic repo",
		RunE: func(cmd *cobra.Command, args []string) error {
			initRestic()
			return nil
		},
	}
	return initCmd
}

func initRestic() {
	fmt.Println("Initializing Restic repo in S3")
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)

	execute.Run(instance.Path, "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "restic/restic", "-r", "s3:"+EnvVariables["AWS_S3_API"]+"/"+EnvVariables["AWS_S3_BUCKET"], "init")
	fmt.Println("")
}
