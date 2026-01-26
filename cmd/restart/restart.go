package restart

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

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var verbose bool
	var devModeFlag bool
	var noDevFlag bool
	var restartCmd = &cobra.Command{
		Use:   "restart",
		Short: "Restart the Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			fmt.Println("Restarting Canasta installation '" + instance.Id + "'...")
			err := Restart(instance, devModeFlag, noDevFlag)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Restarted.")
			return nil
		},
	}
	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	restartCmd.Flags().StringVarP(&instance.Path, "path", "p", workingDir, "Canasta installation directory")
	restartCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	restartCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "compose", "Orchestrator to use for installation")
	restartCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	restartCmd.Flags().BoolVarP(&devModeFlag, "dev", "D", false, "Restart in development mode with Xdebug")
	restartCmd.Flags().BoolVar(&noDevFlag, "no-dev", false, "Restart without development mode (disable dev mode)")
	return restartCmd
}

func Restart(instance config.Installation, enableDev, disableDev bool) error {
	var err error
	if instance.Id != "" {
		instance, err = config.GetDetails(instance.Id)
		if err != nil {
			return err
		}
	}

	// Handle --dev and --no-dev flags
	if enableDev && disableDev {
		return fmt.Errorf("cannot specify both --dev and --no-dev")
	}

	// Migrate to the new version Canasta
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
		// Disable dev mode - just update the config flag
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
