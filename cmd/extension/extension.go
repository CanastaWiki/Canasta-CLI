package extension

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
	instance     config.Installation
	workingDir          string
	wiki         string
	err          error
	verbose      bool
	extensionCmd *cobra.Command
	constants    = extensionsskins.Item{Name: "Canasta extension", RelativeInstallationPath: "canasta-extensions", PhpCommand: "wfLoadExtension"}
)

func NewCmdCreate() *cobra.Command {
	extensionCmd = &cobra.Command{
		Use:   "extension",
		Short: "Manage Canasta extensions",
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			return nil
		},
	}

	if workingDir, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}
	extensionCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	extensionCmd.PersistentFlags().StringVarP(&instance.Path, "path", "p", workingDir, "Canasta installation directory")
	extensionCmd.PersistentFlags().StringVarP(&wiki, "wiki", "w", "", "ID of the specific wiki within the Canasta farm")
	extensionCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")

	extensionCmd.AddCommand(listCmdCreate())
	extensionCmd.AddCommand(enableCmdCreate())
	extensionCmd.AddCommand(disableCmdCreate())

	return extensionCmd
}
