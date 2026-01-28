package start

import (
	"fmt"

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
	var startCmd = &cobra.Command{
		Use:   "start",
		Short: "Start the Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			resolvedInstance, err := canasta.CheckCanastaId(instance)
			if err != nil {
				logging.Fatal(err)
			}
			fmt.Println("Starting Canasta installation '" + resolvedInstance.Id + "'...")
			if err := Start(resolvedInstance, devModeFlag, noDevFlag); err != nil {
				logging.Fatal(err)
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

	var err error
	if enableDev {
		// Enable dev mode using default registry image
		baseImage := canasta.GetDefaultImage()
		if err = devmode.EnableDevMode(instance.Path, instance.Orchestrator, baseImage); err != nil {
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

	// Start containers - orchestrators.Start handles dev mode automatically
	return orchestrators.Start(instance)
}
