package backup

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newDiffCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {
	var snapshot1, snapshot2 string

	diffCmd := &cobra.Command{
		Use:   "diff",
		Short: "Show difference between two backups",
		Long: `Show the differences between two backup snapshots. This compares the file
contents and metadata of both snapshots, displaying added, removed, and
modified files.`,
		Example: `  canasta backup diff -i myinstance --snapshot1 abc123 --snapshot2 def456`,
		RunE: func(cmd *cobra.Command, args []string) error {
			output, err := runBackup(*orch, instance.Path, *envPath, nil, "-r", *repoURL, "diff", snapshot1, snapshot2)
			if err != nil {
				return err
			}
			fmt.Print(output)
			return nil
		},
	}
	diffCmd.Flags().StringVar(&snapshot1, "snapshot1", "", "First snapshot ID (required)")
	diffCmd.Flags().StringVar(&snapshot2, "snapshot2", "", "Second snapshot ID (required)")
	_ = diffCmd.MarkFlagRequired("snapshot1")
	_ = diffCmd.MarkFlagRequired("snapshot2")
	return diffCmd
}
