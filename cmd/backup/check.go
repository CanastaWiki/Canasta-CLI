package backup

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newCheckCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {

	checkCmd := &cobra.Command{
		Use:   "check",
		Short: "Check backup repository integrity",
		Long: `Verify the integrity of the backup repository and its data. This
checks for errors in the repository structure and snapshot data.`,
		Example: `  canasta backup check -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			output, err := runBackup(*orch, instance.Path, *envPath, nil, "-r", *repoURL, "check")
			if err != nil {
				return err
			}
			fmt.Print(output)
			return nil
		},
	}
	return checkCmd
}
