package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

func unlockCmdCreate() *cobra.Command {

	unlockCmd := &cobra.Command{
		Use:   "unlock",
		Short: "Remove locks other processes created",
		Long: `Remove stale lock files from the Restic repository. Use this if a previous
backup operation was interrupted and left the repository in a locked state.`,
		Example: `  canasta restic unlock -i myinstance`,
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
		logging.Fatal(fmt.Errorf("%s", output))
	}
	fmt.Print(output)
}
