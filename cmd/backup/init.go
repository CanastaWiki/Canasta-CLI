package backup

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newInitCmd(orch *orchestrators.Orchestrator, instance *config.Installation, envPath, repoURL *string) *cobra.Command {

	initCmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize a backup repository",
		Long: `Initialize a new backup repository. This must be run once before
creating any backups. The repository location is read from the
RESTIC_REPOSITORY variable (or AWS S3 settings) in the installation's .env file.`,
		Example: `  canasta backup init -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("Initializing backup repository")
			output, err := runBackup(*orch, instance.Path, *envPath, nil, "-r", *repoURL, "init")
			if err != nil {
				return err
			}
			fmt.Print(output)
			return nil
		},
	}
	return initCmd
}
