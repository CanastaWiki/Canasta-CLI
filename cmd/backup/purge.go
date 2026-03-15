package backup

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newPurgeCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {
	var (
		olderThan string
		keepLast  int
		dryRun    bool
	)

	purgeCmd := &cobra.Command{
		Use:   "purge",
		Short: "Remove old backups based on retention policy",
		Long: `Remove backup snapshots that exceed the specified retention policy and
reclaim disk space. At least one retention flag (--older-than or --keep-last)
is required.`,
		Example: `  # Remove backups older than 30 days
  canasta backup purge -i myinstance --older-than 30d

  # Keep only the 10 most recent backups
  canasta backup purge -i myinstance --keep-last 10

  # Combine: keep last 5 and anything within 14 days
  canasta backup purge -i myinstance --older-than 14d --keep-last 5

  # Preview what would be removed
  canasta backup purge -i myinstance --older-than 30d --dry-run`,
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			if olderThan == "" && keepLast == 0 {
				return fmt.Errorf("at least one of --older-than or --keep-last is required")
			}

			args := []string{"-r", *repoURL, "forget", "--group-by", "paths", "--prune"}

			if olderThan != "" {
				args = append(args, "--keep-within", olderThan)
			}
			if keepLast > 0 {
				args = append(args, "--keep-last", fmt.Sprintf("%d", keepLast))
			}
			if dryRun {
				args = append(args, "--dry-run")
			}

			output, err := runBackup(*orch, instance.Path, *envPath, nil, args...)
			if err != nil {
				return err
			}
			fmt.Print(output)
			return nil
		},
	}

	purgeCmd.Flags().StringVar(&olderThan, "older-than", "", "Remove snapshots older than this duration (e.g., 30d, 6m, 1y)")
	purgeCmd.Flags().IntVar(&keepLast, "keep-last", 0, "Always keep the N most recent snapshots")
	purgeCmd.Flags().BoolVar(&dryRun, "dry-run", false, "Show what would be removed without actually removing")
	return purgeCmd
}
