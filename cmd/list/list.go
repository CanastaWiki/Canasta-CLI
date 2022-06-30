package start

import (
	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

func NewCmdCreate() *cobra.Command {
	var instance logging.Installation

	var listCmd = &cobra.Command{
		Use:   "list",
		Short: "list all  Canasta installations",
		RunE: func(cmd *cobra.Command, args []string) error {
			err := List(instance)
			if err != nil {
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
