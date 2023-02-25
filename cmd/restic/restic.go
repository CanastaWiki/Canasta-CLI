package restic

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

var (
	instance      config.Installation
	pwd           string
	err           error
	verbose       bool
	resticCmd     *cobra.Command
	mysqldumpPath = "/mediawiki/config/db.sql"
	commandArgs   = make([]string, 10)
)

func NewCmdCreate() *cobra.Command {
	resticCmd = &cobra.Command{
		Use:   "restic",
		Short: "Use restic to backup and restore Canasta",
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			envPath := instance.Path + "/.env"
			EnvVariables := canasta.GetEnvVariable(envPath)
			commandArgs = append(make([]string, 0), "sudo", "docker", "run", "--rm", "-i", "--env-file", envPath, "restic/restic", "-r", "s3:"+EnvVariables["AWS_S3_API"]+"/"+EnvVariables["AWS_S3_BUCKET"])

			return nil
		},
	}

	resticCmd.AddCommand(initCmdCreate())
	resticCmd.AddCommand(viewSnapshotsCmdCreate())
	resticCmd.AddCommand(takeSnapshotCmdCreate())
	resticCmd.AddCommand(restoreSnapshotCmdCreate())
	resticCmd.AddCommand(forgetSnapshotCmdCreate())
	resticCmd.AddCommand(unlockCmdCreate())
	resticCmd.AddCommand(listFilesCmdCreate())
	resticCmd.AddCommand(checkCmdCreate())
	resticCmd.AddCommand(diffCmdCreate())

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
			if err := os.Mkdir(currentSnapshotFolder, 0o700); err != nil {
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
		if err := os.Mkdir(currentSnapshotFolder, 0o700); err != nil {
			logging.Fatal(err)
		}
		logging.Print("Emptied.. " + currentSnapshotFolder)
	}
}
