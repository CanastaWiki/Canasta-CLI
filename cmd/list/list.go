package list

import (
	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
)

var instance config.Installation

func NewCmdCreate() *cobra.Command {
	var listCmd = &cobra.Command{
		Use:   "list",
		Short: "List all Canasta installations",
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
	config.ListAll()
	return nil
}
