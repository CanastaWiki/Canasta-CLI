package backup

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newListCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List backups",
		Long: `List all snapshots stored in the backup repository. Displays
each snapshot's ID, timestamp, hostname, and tags.`,
		Example: `  canasta backup list -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			output, err := runBackup(*orch, instance.Path, *envPath, nil, "-r", *repoURL, "snapshots")
			if err != nil {
				return err
			}
			fmt.Print(output)
			return nil
		},
	}
	return listCmd
}
