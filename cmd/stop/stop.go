package stop

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmd() *cobra.Command {
	var instance config.Installation
	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	var stopCmd = &cobra.Command{
		Use:   "stop",
		Short: "Shuts down the Canasta installation",
		Long: `Stop all Docker containers for a Canasta installation. The containers
are stopped gracefully, preserving all data in Docker volumes.`,
		Example: `  # Stop an installation by ID
  canasta stop -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) > 0 {
				return fmt.Errorf("unknown argument %q; use --id to specify the instance ID (e.g. canasta stop --id %s)", args[0], args[0])
			}
			resolvedInstance, err := canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			fmt.Println("Stopping Canasta installation '" + resolvedInstance.Id + "'...")
			if err = Stop(resolvedInstance); err != nil {
				return err
			}
			fmt.Println("Stopped.")
			return nil
		},
	}
	stopCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	return stopCmd
}

func Stop(instance config.Installation) error {
	orch, err := orchestrators.NewFromInstance(instance)
	if err != nil {
		return err
	}
	return orch.Stop(instance)
}
