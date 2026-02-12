package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

func viewSnapshotsCmdCreate() *cobra.Command {

	initCmd := &cobra.Command{
		Use:   "view",
		Short: "View restic snapshots",
		Long: `List all snapshots stored in the Restic backup repository. Displays
each snapshot's ID, timestamp, hostname, and tags.`,
		Example: `  canasta restic view -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return viewSnapshots()
		},
	}
	return initCmd
}

func viewSnapshots() error {
	commandArgs = append(commandArgs, "snapshots")
	err, output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	if err != nil {
		return fmt.Errorf("%s", output)
	}
	fmt.Print(output)
	return nil
}
