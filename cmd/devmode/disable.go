package devmode

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	devmodePkg "github.com/CanastaWiki/Canasta-CLI/internal/devmode"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newDisableCmd(instance *config.Installation, orch *orchestrators.Orchestrator) *cobra.Command {
	return &cobra.Command{
		Use:   "disable",
		Short: "Disable development mode",
		Long: `Disable development mode on a Canasta installation. This restores extensions
and skins as real directories and restarts without Xdebug. The mediawiki-code/
directory is preserved so you can re-enable dev mode later without
re-extracting code.`,
		Example: `  # Disable dev mode on an installation
  canasta devmode disable -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Printf("Disabling development mode on '%s'...\n", instance.Id)

			// Disable dev mode (restore symlinks to real directories)
			if err := devmodePkg.DisableDevMode(instance.Path); err != nil {
				return err
			}

			// Update config registry
			instance.DevMode = false
			if instance.Id != "" {
				if err := config.Update(*instance); err != nil {
					logging.Print(fmt.Sprintf("Warning: could not update config: %v\n", err))
				}
			}

			// Regenerate orchestrator config and restart
			if err := (*orch).UpdateConfig(instance.Path); err != nil {
				return err
			}
			if err := (*orch).Stop(*instance); err != nil {
				return err
			}
			if err := (*orch).Start(*instance); err != nil {
				return err
			}

			fmt.Println("Development mode disabled.")
			return nil
		},
	}
}
