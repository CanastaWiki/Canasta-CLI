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
	var instance config.Installation
	workingDir, err := os.Getwd()
	if err != nil {
		logging.Fatal(err)
	}
	instance.Path = workingDir

	var startCmd = &cobra.Command{
		Use:   "start",
		Short: "Start the Canasta installation",
		Long: `Start the Docker containers for a Canasta installation. If the installation
has development mode enabled, it starts with Xdebug automatically. Use
'canasta devmode enable' or 'canasta devmode disable' to change the
development mode setting.`,
		Example: `  # Start an installation by ID
  canasta start -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) > 0 {
				return fmt.Errorf("unknown argument %q; use --id to specify the instance ID (e.g. canasta start --id %s)", args[0], args[0])
			}
			resolvedInstance, err := canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			fmt.Println("Starting Canasta installation '" + resolvedInstance.Id + "'...")
			if err := Start(resolvedInstance); err != nil {
				return err
			}
			fmt.Println("Started.")
			return nil
		},
	}
	startCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	return startCmd
}

func Start(instance config.Installation) error {
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
