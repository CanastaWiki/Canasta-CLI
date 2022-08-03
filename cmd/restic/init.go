package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
)

var (
	tag string
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
	commandArgs = append(commandArgs, "init")
	output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	fmt.Println(output)
}
