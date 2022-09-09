package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
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
	commandArgs = append(commandArgs, "snapshots")
	err, output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	}
	fmt.Print(output)
}
