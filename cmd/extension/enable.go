package extension

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/spf13/cobra"
)

func newEnableCmd(instance *config.Installation, orch *orchestrators.Orchestrator, wiki *string, constants *extensionsskins.Item) *cobra.Command {

	enableCmd := &cobra.Command{
		Use:   "enable EXTENSION1,EXTENSION2,...",
		Short: "Enable a Canasta extension",
		Long: `Enable one or more Canasta extensions by name. Multiple extensions can be
specified as a comma-separated list. Use the --wiki flag to enable an
extension for a specific wiki only.`,
		Example: `  # Enable a single extension
  canasta extension enable VisualEditor -i myinstance

  # Enable multiple extensions at once
  canasta extension enable VisualEditor,Cite,ParserFunctions -i myinstance

  # Enable an extension for a specific wiki
  canasta extension enable VisualEditor -i myinstance -w docs`,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			extensions := strings.Split(args[0], ",")
			for _, extension := range extensions {
				extensionName, err := extensionsskins.CheckInstalled(extension, *instance, *orch, *constants)
				if err != nil {
					fmt.Print(err.Error() + "\n")
					continue
				}
				if err := extensionsskins.Enable(extensionName, *wiki, *instance, *orch, *constants); err != nil {
					return err
				}
			}
			return nil
		},
	}
	return enableCmd
}
