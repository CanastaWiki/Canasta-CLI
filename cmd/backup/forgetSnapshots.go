package backup

import (
	"fmt"

	"github.com/spf13/cobra"
)


func deleteCmdCreate() *cobra.Command {

	deleteCmd := &cobra.Command{
		Use:   "delete",
		Short: "Delete a backup",
		Long: `Remove a snapshot from the backup repository by its ID. The snapshot data
may still exist until a prune is run on the repository.`,
		Example: `  canasta backup delete -i myinstance -s abc123`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return forgetSnapshot()
		},
	}

	deleteCmd.Flags().StringVarP(&snapshot, "snapshot", "s", "", "Snapshot ID (required)")
	_ = deleteCmd.MarkFlagRequired("snapshot")
	return deleteCmd
}

func forgetSnapshot() error {
	output, err := runBackup(nil, "-r", repoURL, "forget", snapshot)
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
