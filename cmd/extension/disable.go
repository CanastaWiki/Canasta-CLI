package extension

import (
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func disableCmdCreate() *cobra.Command {

	disableCmd := &cobra.Command{
		Use:   "disable EXTENSION",
		Short: "Disable a canasta-extension",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			extensionName, err := extensionsskins.CheckEnabled(args[0], instance, constants)
			if err != nil {
				return err
			}
			extensionsskins.Disable(extensionName, instance, constants)
			return err
		},
	}
	return disableCmd
}
