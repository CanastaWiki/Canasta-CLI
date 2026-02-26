package backup

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newFilesCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {
	var snapshot string

	filesCmd := &cobra.Command{
		Use:   "files",
		Short: "List files in a backup",
		Long: `List all files contained in a specific backup snapshot. This is useful
for inspecting what was backed up before performing a restore.`,
		Example: `  canasta backup files -i myinstance -s abc123`,
		RunE: func(cmd *cobra.Command, args []string) error {
			output, err := runBackup(*orch, instance.Path, *envPath, nil, "-r", *repoURL, "ls", snapshot)
			if err != nil {
				return err
			}
			fmt.Print(output)
			return nil
		},
	}
	filesCmd.Flags().StringVarP(&snapshot, "snapshot", "s", "", "Snapshot ID (required)")
	_ = filesCmd.MarkFlagRequired("snapshot")
	return filesCmd
}
