package backup

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newUnlockCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {

	unlockCmd := &cobra.Command{
		Use:   "unlock",
		Short: "Remove locks other processes created",
		Long: `Remove stale lock files from the backup repository. Use this if a previous
backup operation was interrupted and left the repository in a locked state.`,
		Example: `  canasta backup unlock -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			output, err := runBackup(*orch, instance.Path, *envPath, nil, "-r", *repoURL, "unlock")
			if err != nil {
				return err
			}
			fmt.Print(output)
			return nil
		},
	}
	return unlockCmd
}
