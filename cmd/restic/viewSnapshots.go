package restic

import (
	"fmt"

	"github.com/spf13/cobra"
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
	output, err := runRestic(nil, "-r", repoURL, "snapshots")
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
