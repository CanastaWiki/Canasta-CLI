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
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
	}

	resticCmd.AddCommand(initCmdCreate())
	resticCmd.AddCommand(viewSnapshotsCmdCreate())
	resticCmd.AddCommand(takeSnapshotCmdCreate())
	resticCmd.AddCommand(restoreSnapshotCmdCreate())
	resticCmd.AddCommand(forgetSnapshotCmdCreate())
	resticCmd.AddCommand(unlockCmdCreate())

	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}
	resticCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	resticCmd.PersistentFlags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	resticCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	return resticCmd

}

func checkCurrentSnapshotFolder(currentSnapshotFolder string) {

	if _, err := os.Stat(currentSnapshotFolder); err != nil {
		if os.IsNotExist(err) {
			logging.Print("Creating..." + currentSnapshotFolder)
			if err := os.Mkdir(currentSnapshotFolder, os.ModePerm); err != nil {
				logging.Fatal(err)
			}
		} else {
			logging.Fatal(err)
		}
	} else {
		logging.Print("Emptying... " + currentSnapshotFolder)
		if err := os.RemoveAll(currentSnapshotFolder); err != nil {
			logging.Fatal(err)
		}
		if err := os.Mkdir(currentSnapshotFolder, os.ModePerm); err != nil {
			logging.Fatal(err)
		}
		logging.Print("Emptied.. " + currentSnapshotFolder)
	}
}
