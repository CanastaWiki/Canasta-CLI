package skin

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func disableCmdCreate() *cobra.Command {

	disableCmd := &cobra.Command{
		Use:   "disable SKIN_NAME",
		Short: "Disable a Canasta skin",
		Long: `Disable one or more Canasta skins by name. Multiple skins can be specified
as a comma-separated list. Use the --wiki flag to disable a skin for a
specific wiki only.`,
		Example: `  # Disable a skin
  canasta skin disable Timeless -i myinstance

  # Disable a skin for a specific wiki
  canasta skin disable Timeless -i myinstance -w docs`,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			skins := strings.Split(args[0], ",")
			for _, skin := range skins {
				skinName, err := extensionsskins.CheckEnabled(skin, wiki, instance, constants)
				if err != nil {
					fmt.Print(err.Error() + "\n")
					continue
				}
				extensionsskins.Disable(skinName, wiki, instance, constants)
			}
			return err
		},
	}
	return disableCmd
}
