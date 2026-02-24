package config

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
	instance config.Installation
	orch     orchestrators.Orchestrator
	verbose  bool
	err      error
)

func NewCmdCreate() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	configCmd := &cobra.Command{
		Use:   "config",
		Short: "View and modify Canasta configuration",
		Long: `View and modify the .env configuration for a Canasta installation.

Use "canasta config get" to view current settings and "canasta config set"
to change them. The set command handles side effects automatically (e.g.,
updating wikis.yaml when changing HTTPS_PORT) and restarts the instance.
Editing .env by hand may leave the installation in an inconsistent state.

Available settings:

  Network
    HTTP_PORT                       HTTP port (default: 80)
    HTTPS_PORT                      HTTPS port (default: 443)

  MediaWiki
    MW_SECRET_KEY                   Session signing key (auto-generated)

  Database
    MYSQL_PASSWORD                  Root MySQL/MariaDB password
    WIKI_DB_PASSWORD                Per-wiki DB password (auto-generated)
    USE_EXTERNAL_DB                 Skip bundled DB container (default: false)

  PHP
    PHP_UPLOAD_MAX_FILESIZE         Upload file size limit (default: 10M)
    PHP_POST_MAX_SIZE               POST data size limit (default: 10M)
    PHP_MAX_INPUT_VARS              Max input variables (default: 1000)

  Features
    CANASTA_ENABLE_ELASTICSEARCH    Enable Elasticsearch (default: false)
    CANASTA_ENABLE_OBSERVABILITY    Enable observability stack (default: false)

  Caddy / TLS
    CADDY_AUTO_HTTPS                Set to "off" behind a reverse proxy

  Docker Image
    CANASTA_IMAGE                   Override the default Canasta image

  Backup (Restic)
    RESTIC_REPOSITORY               Restic repo URL or local path
    RESTIC_PASSWORD                 Restic repo password
    AWS_ACCESS_KEY_ID               AWS key for S3 backups
    AWS_SECRET_ACCESS_KEY           AWS secret for S3 backups

Note: MW_SITE_SERVER and MW_SITE_FQDN are derived from config/wikis.yaml
and should not be set directly. To change the domain, edit wikis.yaml.
See "canasta config set --help" for side-effect details.`,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			orch, err = orchestrators.NewFromInstance(instance)
			return err
		},
	}

	configCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	configCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose output")

	configCmd.AddCommand(getCmdCreate())
	configCmd.AddCommand(setCmdCreate())

	return configCmd
}
