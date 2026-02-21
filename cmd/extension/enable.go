package extension

import (
	"fmt"
	"strings"

	maintenance "github.com/CanastaWiki/Canasta-CLI/cmd/maintenanceUpdate"
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/spf13/cobra"
)

func enableCmdCreate() *cobra.Command {

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
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			extensions := strings.Split(args[0], ",")
			mainatenanceUpdateRequired := false
			for _, extension := range extensions {
				extensionName, err := extensionsskins.CheckInstalled(extension, instance, orch, constants)
				if err != nil {
					fmt.Print(err.Error() + "\n")
					continue
				}
				if err := extensionsskins.Enable(extensionName, wiki, instance, orch, constants); err != nil {
					return err
				}
				if extensionName == "SemanticMediaWiki" {
					mainatenanceUpdateRequired = true
				}
			}
			if mainatenanceUpdateRequired {
				// Run maintenance update
				if err := maintenance.RunMaintenanceUpdate(instance, wiki); err != nil {
					return fmt.Errorf("maintenance update failed after enabling extension(s): %v", err)
				}
			}
			return nil
		},
	}
	return enableCmd
}
