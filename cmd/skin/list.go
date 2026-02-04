package skin

import (
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func listCmdCreate() *cobra.Command {

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "Lists all the installed Canasta skins",
		Long: `List all Canasta skins available in the installation. Each skin
is shown with its enabled/disabled status.`,
		Example: `  canasta skin list -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			extensionsskins.List(instance, constants)
			return err
		},
	}

	return listCmd
}
