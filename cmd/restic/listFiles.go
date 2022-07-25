package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
)

func listFilesCmdCreate() *cobra.Command {

	listFilesCmd := &cobra.Command{
		Use:   "list",
		Short: "List files in a snapshost",
		RunE: func(cmd *cobra.Command, args []string) error {
			if tag == "" && args[0] == "" {
				return fmt.Errorf("You must provide a restic snapshot tag")
			} else if tag == "" {
				tag = args[0]
			}
			listFiles()
			return nil
		},
	}
	listFilesCmd.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot ID (required)")
	return listFilesCmd
}

func listFiles() {
	envPath := instance.Path + "/.env"
	EnvVariables := canasta.GetEnvVariable(envPath)

	output := execute.Run(instance.Path, "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "restic/restic", "-r", "s3:"+EnvVariables["AWS_S3_API"]+"/"+EnvVariables["AWS_S3_BUCKET"], "ls", tag)
	fmt.Print(output)
}
