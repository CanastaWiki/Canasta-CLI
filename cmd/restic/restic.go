package restic

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

var (
	instance      logging.Installation
	pwd           string
	err           error
	verbose       bool
	resticCmd     *cobra.Command
	mysqldumpPath = "/mediawiki/config/db.sql"
)

func NewCmdCreate() *cobra.Command {
	resticCmd = &cobra.Command{
		Use:   "restic",
		Short: "Use restic to backup and restore Canasta",
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
	}

	resticCmd.AddCommand(initCmdCreate())
	resticCmd.AddCommand(viewSnapshotsCmdCreate())
	resticCmd.AddCommand(takeSnapshotCmdCreate())

	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}
	resticCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	resticCmd.PersistentFlags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	resticCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	return resticCmd

}
