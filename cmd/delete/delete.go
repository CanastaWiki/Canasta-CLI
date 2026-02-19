package start

import (
	"bufio"
	"fmt"
	"log"
	"os"
	"strings"

	"github.com/spf13/cobra"

	backupCmd "github.com/CanastaWiki/Canasta-CLI/cmd/backup"
	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
)

var instance config.Installation

func NewCmdCreate() *cobra.Command {
	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	var yes bool

	var deleteCmd = &cobra.Command{
		Use:   "delete",
		Short: "Delete a Canasta installation",
		Long: `Permanently delete a Canasta installation. This stops and removes all
Docker containers and volumes, deletes all configuration files and data,
and removes the installation from the Canasta registry. You will be
prompted for confirmation before any data is deleted.`,
		Example: `  # Delete an installation by ID
  canasta delete -i myinstance

  # Delete without confirmation prompt
  canasta delete -i myinstance -y`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			var err error
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			if !yes {
				reader := bufio.NewReader(os.Stdin)
				fmt.Printf("This will permanently delete the Canasta installation '%s' and all its data. Continue? [y/N] ", instance.Id)
				text, _ := reader.ReadString('\n')
				text = strings.ToLower(strings.TrimSpace(text))
				if text != "y" {
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
	deleteCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	deleteCmd.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation prompt")
	return deleteCmd
}

func Delete(instance config.Installation) error {
	description := "Deleting Canasta installation '" + instance.Id + "'..."
	_, done := spinner.New(description)
	defer func() {
		done <- struct{}{}
	}()

	orch, err := orchestrators.New(instance.Orchestrator)
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
		if _, err := orch.ExecWithError(instance.Path, "web", cleanupCmd); err != nil {
			logging.Print(fmt.Sprintf("Warning: could not clean up images: %v\n", err))
		}
	}

	//Stopping and deleting Contianers and it's volumes
	if _, err := orch.Destroy(instance.Path); err != nil {
		return err
	}

	//Delete config files
	if _, err := orchestrators.DeleteConfig(instance.Path); err != nil {
		return err
	}

	// Remove any scheduled backup crontab entry
	if removed, err := backupCmd.RemoveSchedule(instance.Id); err != nil {
		logging.Print(fmt.Sprintf("Warning: could not clean up backup schedule: %v\n", err))
	} else if removed {
		logging.Print("Removed backup schedule.\n")
	}

	//Deleting installation details from conf.json
	if err = config.Delete(instance.Id); err != nil {
		return err
	}

	fmt.Println("Deleted.")
	return nil
}
