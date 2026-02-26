package skin

import (
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/spf13/cobra"
)

func newListCmd(instance *config.Installation, orch *orchestrators.Orchestrator, wiki *string, constants *extensionsskins.Item) *cobra.Command {

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "Lists all the installed Canasta skins",
		Long: `List all Canasta skins available in the installation. Each skin
is shown with its enabled/disabled status.`,
		Example: `  canasta skin list -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return extensionsskins.List(*instance, *orch, *constants)
		},
	}

	return listCmd
}
