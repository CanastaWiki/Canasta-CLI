package list

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var (
	instance config.Installation
	cleanup  bool
)

func NewCmdCreate() *cobra.Command {
	var listCmd = &cobra.Command{
		Use:   "list",
		Short: "List all Canasta installations",
		Long: `List all registered Canasta installations. Displays each installation's
ID, path, and orchestrator as recorded in the Canasta configuration file.

Use --cleanup to remove stale entries whose installation directories no longer exist.`,
		Example: `  canasta list
  canasta list --cleanup`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return List(instance, cleanup)
		},
	}
	listCmd.Flags().BoolVar(&cleanup, "cleanup", false, "Remove stale entries whose installation directories no longer exist")
	return listCmd
}

func List(instance config.Installation, cleanup bool) error {
	if cleanup {
		installations, err := config.GetAll()
		if err != nil {
			return err
		}
		for id, installation := range installations {
			if _, err := os.Stat(installation.Path); os.IsNotExist(err) {
				if installation.KindCluster != "" {
					if err := orchestrators.DeleteKindCluster(installation.KindCluster); err != nil {
						fmt.Printf("Warning: failed to delete kind cluster '%s': %s\n", installation.KindCluster, err)
					} else {
						fmt.Printf("Deleted kind cluster '%s'\n", installation.KindCluster)
					}
				}
				if err := config.Delete(id); err != nil {
					return fmt.Errorf("error removing stale entry '%s': %w", id, err)
				}
				fmt.Printf("Removed stale entry '%s' (directory not found)\n", id)
			}
		}
	}
	return config.ListAll()
}
