package start

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/devmode"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var (
	instance    config.Installation
	devModeFlag bool
	noDevFlag   bool
)

func NewCmdCreate() *cobra.Command {
	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	var startCmd = &cobra.Command{
		Use:   "start",
		Short: "Start the Canasta installation",
		Long: `Start the Docker containers for a Canasta installation. If the installation
was created with development mode, it starts with Xdebug enabled by default.
Use --dev to enable or --no-dev to disable development mode at start time.`,
		Example: `  # Start an installation by ID
  canasta start -i myinstance

  # Start with development mode enabled
  canasta start -i myinstance -D

  # Start with development mode disabled
  canasta start -i myinstance --no-dev`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			resolvedInstance, err := canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			fmt.Println("Starting Canasta installation '" + resolvedInstance.Id + "'...")
			if err := Start(resolvedInstance, devModeFlag, noDevFlag); err != nil {
				return err
			}
			fmt.Println("Started.")
			return nil
		},
	}
	startCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	startCmd.Flags().BoolVarP(&devModeFlag, "dev", "D", false, "Start in development mode with Xdebug")
	startCmd.Flags().BoolVar(&noDevFlag, "no-dev", false, "Start without development mode (disable dev mode)")
	return startCmd
}

func Start(instance config.Installation, enableDev, disableDev bool) error {
	// Handle --dev and --no-dev flags
	if enableDev && disableDev {
		return fmt.Errorf("cannot specify both --dev and --no-dev")
	}

	orch, err := orchestrators.NewFromInstance(instance)
	if err != nil {
		return err
	}
	if (enableDev || disableDev) && !orch.SupportsDevMode() {
		return fmt.Errorf("development mode is not supported with %s", orch.Name())
	}
	if enableDev {
		// Enable dev mode using default registry image
		baseImage := canasta.GetDefaultImage()
		if err = devmode.EnableDevMode(instance.Path, orch, baseImage); err != nil {
			return err
		}
		instance.DevMode = true
		if instance.Id != "" {
			if err = config.Update(instance); err != nil {
				logging.Print(fmt.Sprintf("Warning: could not update config: %v\n", err))
			}
		}
	} else if disableDev {
		// Disable dev mode - restore extensions/skins as real directories
		if err = devmode.DisableDevMode(instance.Path); err != nil {
			return err
		}
		instance.DevMode = false
		if instance.Id != "" {
			if err = config.Update(instance); err != nil {
				logging.Print(fmt.Sprintf("Warning: could not update config: %v\n", err))
			}
		}
	}

	// Regenerate orchestrator config (Compose: Caddyfile; K8s: kustomization.yaml)
	if err := orch.UpdateConfig(instance.Path); err != nil {
		return err
	}

	// Start containers
	return orch.Start(instance)
}
