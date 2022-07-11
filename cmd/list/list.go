package start

import (
	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

var instance logging.Installation

func NewCmdCreate() *cobra.Command {
	var listCmd = &cobra.Command{
		Use:   "list",
		Short: "list all  Canasta installations",
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := List(instance); err != nil {
				return err
			}
			return nil
		},
	}
	return listCmd
}

func List(instance logging.Installation) error {
	logging.ListAll()
	return nil
}
