package delete

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	backupCmd "github.com/CanastaWiki/Canasta-CLI/cmd/backup"
	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
)

func NewCmd() *cobra.Command {
	var instance config.Instance
	workingDir, err := os.Getwd()
	if err != nil {
		logging.Fatal(err)
	}
	instance.Path = workingDir

	var yes bool

	var deleteCmd = &cobra.Command{
		Use:   "delete",
		Short: "Delete a Canasta instance",
		Long: `Permanently delete a Canasta instance. This stops and removes all
Docker containers and volumes, deletes all configuration files and data,
and removes the instance from the Canasta registry. You will be
prompted for confirmation before any data is deleted.`,
		Example: `  # Delete an instance by ID
  canasta delete -i myinstance

  # Delete without confirmation prompt
  canasta delete -i myinstance -y`,
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			var err error
			instance, err = canasta.CheckCanastaID(instance)
			if err != nil {
				return err
			}
			if !yes {
				if !canasta.ConfirmAction(fmt.Sprintf("This will permanently delete the Canasta instance '%s' and all its data. Continue? [y/N] ", instance.ID)) {
					fmt.Println("Operation cancelled.")
					return nil
				}
			}
			if err := Delete(instance); err != nil {
				return err
			}
			return nil
		},
	}
	deleteCmd.Flags().StringVarP(&instance.ID, "id", "i", "", "Canasta instance ID (defaults to instance associated with current directory)")
	deleteCmd.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation prompt")
	return deleteCmd
}

func Delete(instance config.Instance) error {
	description := "Deleting Canasta instance '" + instance.ID + "'..."
	stopSpinner := spinner.New(description)
	defer stopSpinner() // ensure cleanup on error paths

	orch, err := orchestrators.NewFromInstance(instance)
	if err != nil {
		return err
	}

	// Ensure containers are running so we can clean up images from inside
	// (needed on Linux where container-created files are owned by www-data)
	ensureErr := orch.CheckRunningStatus(instance)
	if ensureErr != nil {
		logging.Print("Containers not running, starting them...\n")
		ensureErr = orch.Start(instance)
	}
	if ensureErr != nil {
		fmt.Println("Warning: could not start containers for image cleanup.")
		fmt.Println("Some image files may be orphaned and require manual removal with sudo.")
	} else {
		// Clean up images from inside the container before stopping
		cleanupCmd := "find /mediawiki/images -mindepth 1 -delete"
		if _, err := orch.ExecWithError(instance.Path, orchestrators.ServiceWeb, cleanupCmd); err != nil {
			logging.Print(fmt.Sprintf("Warning: could not clean up images: %v\n", err))
		}
	}

	// Stopping and deleting Containers and their volumes
	if _, err := orch.Destroy(instance.Path); err != nil {
		return err
	}

	// Delete config files
	if _, err := orchestrators.DeleteConfig(instance.Path); err != nil {
		return err
	}

	// Remove any scheduled backup crontab entry
	if removed, err := backupCmd.RemoveSchedule(instance.ID); err != nil {
		logging.Print(fmt.Sprintf("Warning: could not clean up backup schedule: %v\n", err))
	} else if removed {
		logging.Print("Removed backup schedule.\n")
	}

	// Deleting instance details from conf.json
	if err = config.Delete(instance.ID); err != nil {
		return err
	}

	stopSpinner()
	fmt.Println("Deleted.")
	return nil
}
