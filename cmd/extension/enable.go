package extension

import (
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func enableCmdCreate() *cobra.Command {

	enableCmd := &cobra.Command{
		Use:   "enable EXTENSION",
		Short: "Enable a canasta-extension",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			extensionName, err := extensionsskins.CheckInstalled(args[0], instance, constants)
			if err != nil {
				return err
			}
			extensionsskins.Enable(extensionName, instance, constants)
			return err
		},
	}
	return enableCmd
}
