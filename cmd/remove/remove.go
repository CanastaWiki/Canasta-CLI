package remove

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/cmd/restart"
	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmd() *cobra.Command {
	var instance config.Instance
	var wikiID string
	var yes bool

	workingDir, err := os.Getwd()
	if err != nil {
		logging.Fatal(err)
	}
	instance.Path = workingDir

	addCmd := &cobra.Command{
		Use:   "remove",
		Short: "Remove a wiki from a Canasta instance",
		Long: `Remove a wiki from an existing Canasta instance. This deletes the wiki's
database, settings files, uploaded images, and its entry in wikis.yaml, then
regenerates the Caddyfile and restarts the instance. You will be prompted
for confirmation before any data is deleted.`,
		Example: `  # Remove a wiki by ID
  canasta remove -i myinstance -w docs

  # Remove without confirmation prompt
  canasta remove -i myinstance -w docs -y`,
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			instance, err = canasta.CheckCanastaID(instance)
			if err != nil {
				return err
			}

			fmt.Printf("Removing wiki '%s' from Canasta instance '%s'...\n", wikiID, instance.ID)
			if err := RemoveWiki(instance, wikiID, yes); err != nil {
				return err
			}
			fmt.Println("Done.")
			return nil
		},
	}

	addCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "ID of the wiki")
	addCmd.Flags().StringVarP(&instance.ID, "id", "i", "", "Canasta instance ID (defaults to instance associated with current directory)")
	addCmd.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation prompt")

	_ = addCmd.MarkFlagRequired("wiki")

	return addCmd
}

// RemoveWiki removes a wiki with the given wikiID from a Canasta instance.
func RemoveWiki(instance config.Instance, wikiID string, yes bool) error {
	orch, err := orchestrators.New(instance.Orchestrator)
	if err != nil {
		return err
	}

	// Ensure containers are running (starts them if needed)
	// Needed for database removal and image cleanup on Linux
	containersRunning := true
	ensureErr := orch.CheckRunningStatus(instance)
	if ensureErr != nil {
		logging.Print("Containers not running, starting them...\n")
		ensureErr = orch.Start(instance)
	}
	if ensureErr != nil {
		containersRunning = false
		fmt.Println("Warning: could not start containers.")
		fmt.Println("Database may not be removed and some image files may be orphaned.")
		fmt.Println("These may require manual removal.")
	}

	// Checking Wiki existence
	exists, err := farmsettings.WikiIDExists(instance.Path, wikiID)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("a wiki with the ID '%s' does not exist", wikiID)
	}

	if !yes {
		if !canasta.ConfirmAction("This will delete the wiki " + wikiID + " in the Canasta instance " + instance.ID + " and the corresponding database. Continue? [y/N] ") {
			fmt.Println("Operation cancelled.")
			return nil
		}
	}

	// Remove the wiki
	err = farmsettings.RemoveWiki(wikiID, instance.Path)
	if err != nil {
		return err
	}

	// Update wikis.yaml.template if gitops is active.
	if err := gitops.SyncWikisTemplate(instance.Path); err != nil {
		return err
	}

	// Remove the corresponding Database (requires running container)
	if containersRunning {
		err = mediawiki.RemoveDatabase(instance.Path, wikiID, orch)
		if err != nil {
			return err
		}
	}

	// Remove the Localsettings
	err = canasta.RemoveSettings(instance.Path, wikiID)
	if err != nil {
		return err
	}

	// Remove the Images (from inside container first to handle www-data ownership on Linux)
	if containersRunning {
		cleanupCmd := fmt.Sprintf("rm -rf %s", orchestrators.ShellQuote("/mediawiki/images/"+wikiID))
		_, _ = orch.ExecWithError(instance.Path, orchestrators.ServiceWeb, cleanupCmd)
	}
	err = canasta.RemoveImages(instance.Path, wikiID)
	if err != nil {
		return err
	}

	// Remove the Public Assets
	err = canasta.RemovePublicAssets(instance.Path, wikiID)
	if err != nil {
		return err
	}

	// Rewrite the Caddyfile
	err = orch.UpdateConfig(instance.Path)
	if err != nil {
		return err
	}

	// Restart the Canasta Instance
	err = restart.Restart(instance)
	if err != nil {
		return err
	}

	fmt.Println("Successfully removed wiki " + wikiID + " from Canasta instance " + instance.ID + ".")

	return nil
}
