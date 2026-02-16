package backup

import (
	"fmt"

	"github.com/spf13/cobra"
)

func listCmdCreate() *cobra.Command {

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List backups",
		Long: `List all snapshots stored in the backup repository. Displays
each snapshot's ID, timestamp, hostname, and tags.`,
		Example: `  canasta backup list -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return listBackups()
		},
	}
	return listCmd
}

func listBackups() error {
	output, err := runRestic(nil, "-r", repoURL, "snapshots")
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
