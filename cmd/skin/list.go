package skin

import (
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func listCmdCreate() *cobra.Command {

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "Lists all the installed skins",
		RunE: func(cmd *cobra.Command, args []string) error {
			extensionsskins.List(instance, constants)
			return err
		},
	}

	return listCmd
}
