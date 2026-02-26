package backup

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newDeleteCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {
	var snapshot string

	deleteCmd := &cobra.Command{
		Use:   "delete",
		Short: "Delete a backup",
		Long: `Remove a snapshot from the backup repository by its ID. The snapshot data
may still exist until a prune is run on the repository.`,
		Example: `  canasta backup delete -i myinstance -s abc123`,
		RunE: func(cmd *cobra.Command, args []string) error {
			output, err := runBackup(*orch, instance.Path, *envPath, nil, "-r", *repoURL, "forget", snapshot)
			if err != nil {
				return err
			}
			fmt.Print(output)
			return nil
		},
	}

	deleteCmd.Flags().StringVarP(&snapshot, "snapshot", "s", "", "Snapshot ID (required)")
	_ = deleteCmd.MarkFlagRequired("snapshot")
	return deleteCmd
}
