package extension

import (
	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
)

func NewCmd() *cobra.Command {
	return extensionsskins.NewCmd(extensionsskins.Item{
		Name:                     "Canasta extension",
		CmdName:                  "extension",
		Plural:                   "extensions",
		RelativeInstallationPath: "extensions",
		PhpCommand:               "wfLoadExtension",
		ExampleNames:             "VisualEditor,Cite,ParserFunctions",
	})
}
