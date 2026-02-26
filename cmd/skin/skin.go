package skin

import (
	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
)

func NewCmd() *cobra.Command {
	return extensionsskins.NewCmd(extensionsskins.Item{
		Name:                     "Canasta skin",
		CmdName:                  "skin",
		Plural:                   "skins",
		RelativeInstallationPath: "skins",
		PhpCommand:               "wfLoadSkin",
		ExampleNames:             "Timeless",
	})
}
