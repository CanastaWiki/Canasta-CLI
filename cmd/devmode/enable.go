package devmode

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	devmodePkg "github.com/CanastaWiki/Canasta-CLI/internal/devmode"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newEnableCmd(instance *config.Instance, orch *orchestrators.Orchestrator) *cobra.Command {
	return &cobra.Command{
		Use:   "enable",
		Short: "Enable development mode",
		Long: `Enable development mode on an existing Canasta instance. This extracts
MediaWiki code for live editing, builds an Xdebug-enabled image, and restarts
the instance with dev mode compose files. Only supported with Docker Compose.`,
		Example: `  # Enable dev mode on an instance
  canasta devmode enable -i myinstance`,
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			fmt.Printf("Enabling development mode on '%s'...\n", instance.ID)

			// Determine the base image: check .env for CANASTA_IMAGE, fall back to default
			baseImage := canasta.GetBaseImage(instance.Path)

			// Enable dev mode (extract code, create files, build xdebug image)
			if err := devmodePkg.EnableDevMode(instance.Path, *orch, baseImage); err != nil {
				return err
			}

			// Update config registry
			instance.DevMode = true
			if instance.ID != "" {
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

			fmt.Println("\033[32mDevelopment mode enabled. Edit files in mediawiki-code/ - changes appear immediately.\033[0m")
			fmt.Println("\033[32mVSCode: Open the instance directory, install PHP Debug extension, and start 'Listen for Xdebug'.\033[0m")
			return nil
		},
	}
}
