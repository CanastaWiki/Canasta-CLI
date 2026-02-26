package skin

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/spf13/cobra"
)

func newDisableCmd(instance *config.Installation, orch *orchestrators.Orchestrator, wiki *string, constants *extensionsskins.Item) *cobra.Command {

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
				skinName, err := extensionsskins.CheckEnabled(skin, *wiki, *instance, *orch, *constants)
				if err != nil {
					fmt.Print(err.Error() + "\n")
					continue
				}
				if err := extensionsskins.Disable(skinName, *wiki, *instance, *orch, *constants); err != nil {
					return err
				}
			}
			return nil
		},
	}
	return disableCmd
}
