package export

import (
	"fmt"
	"log"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmd() *cobra.Command {
	var instance config.Installation
	var wikiID string
	var outputPath string

	exportCmd := &cobra.Command{
		Use:   "export",
		Short: "Export the database of a wiki in a Canasta instance",
		Long: `Export a wiki's database as a SQL dump file. The instance must be running.
By default the dump is saved to <wikiID>.sql in the current directory.
Use a .gz extension on the output path to get a gzip-compressed dump.`,
		Example: `  # Export a wiki's database to the default file
  canasta export -i myinstance -w main

  # Export to a specific file
  canasta export -i myinstance -w main -f /backups/main-backup.sql

  # Export as gzipped SQL
  canasta export -i myinstance -w main -f /backups/main-backup.sql.gz`,
		RunE: func(cmd *cobra.Command, args []string) error {
			var err error

			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}

			// Check containers are running
			orch, err := orchestrators.New(instance.Orchestrator)
			if err != nil {
				return err
			}
			err = orch.CheckRunningStatus(instance)
			if err != nil {
				return err
			}

			// Verify the wiki exists
			exists, err := farmsettings.WikiIDExists(instance.Path, wikiID)
			if err != nil {
				return err
			}
			if !exists {
				return fmt.Errorf("wiki '%s' does not exist in Canasta instance '%s'", wikiID, instance.Id)
			}

			// Default output path
			if outputPath == "" {
				outputPath = wikiID + ".sql"
			}

			fmt.Printf("Exporting database for wiki '%s'...\n", wikiID)
			if err := exportDatabase(instance, wikiID, outputPath); err != nil {
				return err
			}
			fmt.Printf("Database exported to %s\n", outputPath)
			return nil
		},
	}

	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	exportCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	exportCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "ID of the wiki to export")
	exportCmd.Flags().StringVarP(&outputPath, "file", "f", "", "Output file path (default: <wikiID>.sql)")

	_ = exportCmd.MarkFlagRequired("wiki")

	return exportCmd
}

func exportDatabase(instance config.Installation, wikiID, outputPath string) error {
	orch, err := orchestrators.New(instance.Orchestrator)
	if err != nil {
		return err
	}

	// Read the database password from .env
	envVariables, err := canasta.GetEnvVariable(instance.Path + "/.env")
	if err != nil {
		return err
	}
	dbPassword := envVariables["MYSQL_PASSWORD"]
	if dbPassword == "" {
		dbPassword = "mediawiki"
	}

	// Escape single quotes in password for shell safety
	escapedPassword := strings.ReplaceAll(dbPassword, "'", "'\\''")

	tempFile := fmt.Sprintf("/tmp/%s.sql", wikiID)

	// Run mysqldump inside the db container (no --databases flag to avoid USE statements)
	dumpCmd := fmt.Sprintf("mysqldump -u root -p'%s' %s > %s", escapedPassword, wikiID, tempFile)
	output, err := orch.ExecWithError(instance.Path, "db", dumpCmd)
	if err != nil {
		return fmt.Errorf("failed to export database: %s", output)
	}

	// Compress the dump if the output filename ends in .gz
	copyFile := tempFile
	if strings.HasSuffix(outputPath, ".gz") {
		gzipCmd := fmt.Sprintf("gzip -f %s", tempFile)
		output, err = orch.ExecWithError(instance.Path, "db", gzipCmd)
		if err != nil {
			return fmt.Errorf("failed to compress export file: %s", output)
		}
		copyFile = tempFile + ".gz"
	}

	// Copy the dump file from the container to the host
	err = orch.CopyFrom(instance.Path, "db", copyFile, outputPath)
	if err != nil {
		return fmt.Errorf("failed to copy export file from container: %w", err)
	}

	// Clean up temp files in the container
	rmCmd := fmt.Sprintf("rm -f %s %s.gz", tempFile, tempFile)
	_, _ = orch.ExecWithError(instance.Path, "db", rmCmd)

	return nil
}
