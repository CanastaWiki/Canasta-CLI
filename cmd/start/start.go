package start

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmd() *cobra.Command {
	var instance config.Instance
	workingDir, err := os.Getwd()
	if err != nil {
		logging.Fatal(err)
	}
	instance.Path = workingDir

	var startCmd = &cobra.Command{
		Use:   "start",
		Short: "Start the Canasta instance",
		Long: `Start the Docker containers for a Canasta instance. If the instance
has development mode enabled, it starts with Xdebug automatically. Use
'canasta devmode enable' or 'canasta devmode disable' to change the
development mode setting.`,
		Example: `  # Start an instance by ID
  canasta start -i myinstance`,
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			resolvedInstance, err := canasta.CheckCanastaID(instance)
			if err != nil {
				return err
			}
			fmt.Println("Starting Canasta instance '" + resolvedInstance.ID + "'...")
			if err := Start(resolvedInstance); err != nil {
				return err
			}
			fmt.Println("Started.")
			return nil
		},
	}
	startCmd.Flags().StringVarP(&instance.ID, "id", "i", "", "Canasta instance ID (defaults to instance associated with current directory)")
	return startCmd
}

func Start(instance config.Instance) error {
	orch, err := orchestrators.NewFromInstance(instance)
	if err != nil {
		return err
	}

	// Regenerate orchestrator config (Compose: Caddyfile; K8s: kustomization.yaml)
	if err := orch.UpdateConfig(instance.Path); err != nil {
		return err
	}

	// Start containers
	return orch.Start(instance)
}
