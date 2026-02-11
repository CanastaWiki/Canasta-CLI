package skin

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func enableCmdCreate() *cobra.Command {

	enableCmd := &cobra.Command{
		Use:   "enable SKIN_NAME",
		Short: "Enable a Canasta skin",
		Long: `Enable one or more Canasta skins by name. Multiple skins can be specified
as a comma-separated list. Use the --wiki flag to enable a skin for a
specific wiki only.`,
		Example: `  # Enable a skin
  canasta skin enable Timeless -i myinstance

  # Enable a skin for a specific wiki
  canasta skin enable Timeless -i myinstance -w docs`,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			skins := strings.Split(args[0], ",")
			for _, skin := range skins {
				skinName, err := extensionsskins.CheckInstalled(skin, instance, orch, constants)
				if err != nil {
					fmt.Print(err.Error() + "\n")
					continue
				}
				extensionsskins.Enable(skinName, wiki, instance, orch, constants)
			}
			return err
		},
	}
	return enableCmd
}
