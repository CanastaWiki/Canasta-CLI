package backup

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

func createBackupCmdCreate() *cobra.Command {

	createBackupCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a backup",
		Long: `Create a new backup snapshot of the Canasta installation. This dumps the
database, stages configuration files, extensions, images, and skins into
a Docker volume, then uploads the snapshot to the backup repository with
the specified tag.`,
		Example: `  # Create a backup with a descriptive tag
  canasta backup create -i myinstance -t before-upgrade`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return takeSnapshot(tag)
		},
	}

	createBackupCmd.Flags().StringVarP(&tag, "tag", "t", "", "Backup tag (required)")
	_ = createBackupCmd.MarkFlagRequired("tag")
	return createBackupCmd
}

func takeSnapshot(tag string) error {
	fmt.Printf("Taking snapshot '%s'...\n", tag)
	EnvVariables, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return err
	}

	_, err = orch.ExecWithError(instance.Path, "web", fmt.Sprintf("mysqldump -h db -u root -p%s --databases %s > %s", EnvVariables["MYSQL_PASSWORD"], EnvVariables["WG_DB_NAME"], mysqldumpPath))
	if err != nil {
		return fmt.Errorf("mysqldump failed: %w", err)
	}
	logging.Print("mysqldump mediawiki completed")

	volumes := make(map[string]string)
	for _, dir := range []string{"config", "extensions", "images", "skins"} {
		volumes[filepath.Join(instance.Path, dir)] = "/currentsnapshot/" + dir
	}

	hostname, _ := os.Hostname()
	logging.Print("Staging files to backup volume...")
	output, err := runBackup(volumes, "-r", repoURL, "--tag", fmt.Sprintf("%s__on__%s", tag, hostname), "backup", "/currentsnapshot")
	if err != nil {
		return err
	}
	fmt.Print(output)
	fmt.Println("Backup completed")
	return nil
}
