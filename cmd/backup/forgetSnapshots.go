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
		Example: `  canasta backup delete -i myinstance -t abc123`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return forgetSnapshot()
		},
	}

	deleteCmd.Flags().StringVarP(&tag, "tag", "t", "", "Snapshot ID (required)")
	_ = deleteCmd.MarkFlagRequired("tag")
	return deleteCmd
}

func forgetSnapshot() error {
	output, err := runBackup(nil, "-r", repoURL, "forget", tag)
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
