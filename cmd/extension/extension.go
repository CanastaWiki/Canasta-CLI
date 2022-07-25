package extension

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

var (
	instance     logging.Installation
	pwd          string
	err          error
	verbose      bool
	extensionCmd *cobra.Command
)

func NewCmdCreate() *cobra.Command {
	extensionCmd = &cobra.Command{
		Use:   "extension",
		Short: "Manage extensions",
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
	}

	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}
	extensionCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	extensionCmd.PersistentFlags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	extensionCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")

	extensionCmd.AddCommand(listCmdCreate())
	extensionCmd.AddCommand(enableCmdCreate())
	extensionCmd.AddCommand(disableCmdCreate())

	return extensionCmd
}

func contains(list []string, element string) bool {
	for _, item := range list {
		if item == element {
			return true
		}
	}
	return false
}
