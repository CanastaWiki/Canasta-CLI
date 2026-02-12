package list

import (
	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
)

var instance config.Installation

func NewCmdCreate() *cobra.Command {
	var listCmd = &cobra.Command{
		Use:   "list",
		Short: "List all Canasta installations",
		Long: `List all registered Canasta installations. Displays each installation's
ID, path, and orchestrator as recorded in the Canasta configuration file.`,
		Example: `  canasta list`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := List(instance); err != nil {
				return err
			}
			return nil
		},
	}
	return listCmd
}

func List(instance config.Installation) error {
	return config.ListAll()
}
