package extension

import (
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/spf13/cobra"
)

func newListCmd(instance *config.Installation, orch *orchestrators.Orchestrator, wiki *string, constants *extensionsskins.Item) *cobra.Command {

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "Lists all the installed Canasta extensions",
		Long: `List all Canasta extensions available in the installation. Each extension
is shown with its enabled/disabled status.`,
		Example: `  canasta extension list -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return extensionsskins.List(*instance, *orch, *constants)
		},
	}

	return listCmd
}
