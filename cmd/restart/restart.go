package restart

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/compatibility"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/devmode"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var verbose bool
	var devModeFlag bool
	var noDevFlag bool

	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	var restartCmd = &cobra.Command{
		Use:   "restart",
		Short: "Restart the Canasta installation",
		Long: `Restart a Canasta installation by stopping and then starting all Docker
containers. Any pending configuration migrations are applied during the
restart. Use --dev or --no-dev to change the development mode setting.`,
		Example: `  # Restart an installation by ID
  canasta restart -i myinstance

  # Restart and enable development mode
  canasta restart -i myinstance -D`,
		RunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			resolvedInstance, err := canasta.CheckCanastaId(instance)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Restarting Canasta installation '" + resolvedInstance.Id + "'...")
			err = Restart(resolvedInstance, devModeFlag, noDevFlag)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Restarted.")
			return nil
		},
	}
	restartCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	restartCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	restartCmd.Flags().BoolVarP(&devModeFlag, "dev", "D", false, "Restart in development mode with Xdebug")
	restartCmd.Flags().BoolVar(&noDevFlag, "no-dev", false, "Restart without development mode (disable dev mode)")
	return restartCmd
}

func Restart(instance config.Installation, enableDev, disableDev bool) error {
	// Check version compatibility (warning for non-destructive command)
	if warning := compatibility.CheckCompatibility(instance); warning != "" {
		fmt.Println(warning)
		fmt.Println()
	}

	// Handle --dev and --no-dev flags
	if enableDev && disableDev {
		return fmt.Errorf("cannot specify both --dev and --no-dev")
	}

	// Migrate to the new version Canasta
	var err error
	if err = canasta.MigrateToNewVersion(instance.Path); err != nil {
		return err
	}

	// Stop containers (orchestrators.Stop handles dev mode automatically)
	if err = orchestrators.Stop(instance); err != nil {
		return err
	}

	// Handle dev mode enable/disable
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

	// Start containers (orchestrators.Start handles dev mode automatically)
	return orchestrators.Start(instance)
}
