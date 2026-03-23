package list

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmd() *cobra.Command {
	var instance config.Instance
	var cleanup bool

	var listCmd = &cobra.Command{
		Use:   "list",
		Short: "List all Canasta instances",
		Long: `List all registered Canasta instances. Displays each instance's
ID, path, and orchestrator as recorded in the Canasta configuration file.

Use --cleanup to remove stale entries whose instance directories no longer exist.`,
		Example: `  canasta list
  canasta list --cleanup`,
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return List(instance, cleanup)
		},
	}
	listCmd.Flags().BoolVar(&cleanup, "cleanup", false, "Remove stale entries whose instance directories no longer exist")
	return listCmd
}

func List(_ config.Instance, cleanup bool) error {
	if cleanup {
		instances, err := config.GetAll()
		if err != nil {
			return err
		}
		for id, inst := range instances {
			if _, err := os.Stat(inst.Path); os.IsNotExist(err) {
				if inst.KindCluster != "" {
					if err := orchestrators.DeleteKindCluster(inst.KindCluster); err != nil {
						fmt.Printf("Warning: failed to delete kind cluster '%s': %s\n", inst.KindCluster, err)
					} else {
						fmt.Printf("Deleted kind cluster '%s'\n", inst.KindCluster)
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
