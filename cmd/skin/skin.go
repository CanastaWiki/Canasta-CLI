package skin

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/extensionsskins"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

var (
	instance  config.Installation
	wiki      string
	err       error
	verbose   bool
	skinCmd   *cobra.Command
	constants = extensionsskins.Item{Name: "Canasta skin", RelativeInstallationPath: "canasta-skins", PhpCommand: "wfLoadSkin"}
)

func NewCmdCreate() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	skinCmd = &cobra.Command{
		Use:   "skin",
		Short: "Manage Canasta skins",
		Long: `Manage MediaWiki skins in a Canasta installation. Subcommands allow you
to list all available skins, and enable or disable them globally or for
a specific wiki in a farm.`,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
	}

	skinCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	skinCmd.PersistentFlags().StringVarP(&wiki, "wiki", "w", "", "ID of the specific wiki within the Canasta farm")
	skinCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")

	skinCmd.AddCommand(listCmdCreate())
	skinCmd.AddCommand(enableCmdCreate())
	skinCmd.AddCommand(disableCmdCreate())

	return skinCmd
}
