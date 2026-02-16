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
			if tag == "" && args[0] == "" {
				return fmt.Errorf("You must provide a snapshot ID")
			} else if tag == "" {
				tag = args[0]
			}
			return forgetSnapshot()
		},
	}

	deleteCmd.Flags().StringVarP(&tag, "tag", "t", "", "Snapshot ID (required)")
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
