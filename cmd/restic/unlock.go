package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
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
	commandArgs = append(commandArgs, "unlock")
	err, output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	}
	fmt.Print(output)
}
