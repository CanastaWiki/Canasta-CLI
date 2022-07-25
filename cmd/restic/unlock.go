package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
)

func unlockCmdCreate() *cobra.Command {

	unlockCmd := &cobra.Command{
		Use:   "unlock",
		Short: "Remove locks other processes created",
		Run: func(cmd *cobra.Command, args []string) {
			unlock()
		},
	}
	return unlockCmd
}

func unlock() {
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)

	output := execute.Run(instance.Path, "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "restic/restic", "-r", "s3:"+EnvVariables["AWS_S3_API"]+"/"+EnvVariables["AWS_S3_BUCKET"], "unlock")
	fmt.Print(output)
}
