package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
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
	err, output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	} else {
		fmt.Print(output)
	}
}
