package extension

import (
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func listCmdCreate() *cobra.Command {

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "Lists all the installed Canasta extensions",
		Long: `List all Canasta extensions available in the installation. Each extension
is shown with its enabled/disabled status.`,
		Example: `  canasta extension list -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			extensionsskins.List(instance, orch, constants)
			return err
		},
	}

	return listCmd
}
