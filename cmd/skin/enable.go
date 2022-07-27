package skin

import (
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func enableCmdCreate() *cobra.Command {

	enableCmd := &cobra.Command{
		Use:   "enable SKIN_NAME",
		Short: "Enable a canasta-skin",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			skinName, err := extensionsskins.CheckInstalled(args[0], instance, constants)
			if err != nil {
				return err
			}
			extensionsskins.Enable(skinName, instance, constants)
			return err
		},
	}
	return enableCmd
}
