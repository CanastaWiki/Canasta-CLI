package restic

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var (
	instance      config.Installation
	err           error
	verbose       bool
	resticCmd     *cobra.Command
	mysqldumpPath = "/mediawiki/config/db.sql"
	orch          orchestrators.Orchestrator
	envPath       string
	repoURL       string
)

func NewCmdCreate() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	resticCmd = &cobra.Command{
		Use:   "restic",
		Short: "Use restic to backup and restore Canasta",
		Long: `Manage backups of a Canasta installation using Restic. Subcommands allow you
to initialize a backup repository, take and restore snapshots, view and compare
snapshots, and schedule recurring backups. Requires RESTIC_REPOSITORY (or
AWS S3 settings) and RESTIC_PASSWORD to be configured in the installation's .env file.`,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			envPath = instance.Path + "/.env"
			EnvVariables, envErr := canasta.GetEnvVariable(envPath)
			if envErr != nil {
				return envErr
			}
			repoURL = getRepoURL(EnvVariables)

			orch, err = orchestrators.New(instance.Orchestrator)
			if err != nil {
				return err
			}
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
	resticCmd.AddCommand(scheduleCmdCreate())

	resticCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	resticCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	return resticCmd

}

func getRepoURL(env map[string]string) string {
	if val, ok := env["RESTIC_REPOSITORY"]; ok && val != "" {
		return val
	}
	if val, ok := env["RESTIC_REPO"]; ok && val != "" {
		return val
	}
	return "s3:" + env["AWS_S3_API"] + "/" + env["AWS_S3_BUCKET"]
}

func checkCurrentSnapshotFolder(currentSnapshotFolder string) error {
	if _, err := os.Stat(currentSnapshotFolder); err != nil {
		if os.IsNotExist(err) {
			logging.Print("Creating..." + currentSnapshotFolder)
			if err := os.Mkdir(currentSnapshotFolder, 0o700); err != nil {
				return err
			}
		} else {
			return err
		}
	} else {
		logging.Print("Emptying... " + currentSnapshotFolder)
		if err := os.RemoveAll(currentSnapshotFolder); err != nil {
			return err
		}
		if err := os.Mkdir(currentSnapshotFolder, 0o700); err != nil {
			return err
		}
		logging.Print("Emptied.. " + currentSnapshotFolder)
	}
	return nil
}

// runRestic is a convenience wrapper for orch.RunRestic
// using the package-level orchestrator, install path, and env path.
func runRestic(volumes map[string]string, args ...string) (string, error) {
	return orch.RunRestic(instance.Path, envPath, volumes, args...)
}
