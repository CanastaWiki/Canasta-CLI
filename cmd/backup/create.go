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
		Long: `Create a new backup snapshot of the Canasta installation. This dumps each
wiki's database (read from wikis.yaml), stages configuration files,
extensions, images, skins, and public_assets into a Docker volume, along
with .env, docker-compose.override.yml, and my.cnf (if present), then
uploads the snapshot to the backup repository with the specified tag.`,
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

	wikiIDs, err := getWikiIDs(instance.Path)
	if err != nil {
		return err
	}

	// Create the backup directory inside the container for database dumps
	_, err = orch.ExecWithError(instance.Path, "web", "mkdir -p /mediawiki/config/backup")
	if err != nil {
		return fmt.Errorf("failed to create backup directory: %w", err)
	}

	for _, id := range wikiIDs {
		logging.Print(fmt.Sprintf("Dumping database for wiki '%s'...", id))
		cmd := fmt.Sprintf("mysqldump -h db -u root -p%s --databases %s > %s",
			EnvVariables["MYSQL_PASSWORD"], id, dumpPath(id))
		_, err = orch.ExecWithError(instance.Path, "web", cmd)
		if err != nil {
			return fmt.Errorf("mysqldump failed for wiki '%s': %w", id, err)
		}
	}
	logging.Print("Database dumps completed")

	volumes := make(map[string]string)
	for _, dir := range []string{"config", "extensions", "images", "skins", "public_assets"} {
		volumes[filepath.Join(instance.Path, dir)] = "/currentsnapshot/" + dir
	}
	for _, file := range []string{".env", "docker-compose.override.yml", "my.cnf"} {
		src := filepath.Join(instance.Path, file)
		if _, statErr := os.Stat(src); statErr == nil {
			volumes[src] = "/currentsnapshot/" + file
		}
	}

	hostname, _ := os.Hostname()
	logging.Print("Staging files to backup volume...")
	output, err := runBackup(volumes, "-r", repoURL, "--tag", fmt.Sprintf("%s__on__%s", tag, hostname), "backup", "/currentsnapshot")
	if err != nil {
		return err
	}
	fmt.Print(output)

	// Clean up the backup directory containing database dumps
	os.RemoveAll(filepath.Join(instance.Path, "config", "backup"))

	fmt.Println("Backup completed")
	return nil
}
