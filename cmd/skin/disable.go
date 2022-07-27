package skin

import (
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func disableCmdCreate() *cobra.Command {

	disableCmd := &cobra.Command{
		Use:   "disable SKIN_NAME",
		Short: "Disable a canasta-skin",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			skinName, err := extensionsskins.CheckEnabled(args[0], instance, constants)
			if err != nil {
				return err
			}
			extensionsskins.Disable(skinName, instance, constants)
			return err
		},
	}
	return disableCmd
}
