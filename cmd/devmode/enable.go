package devmode

import (
	"fmt"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	devmodePkg "github.com/CanastaWiki/Canasta-CLI/internal/devmode"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newEnableCmd(instance *config.Installation, orch *orchestrators.Orchestrator) *cobra.Command {
	return &cobra.Command{
		Use:   "enable",
		Short: "Enable development mode",
		Long: `Enable development mode on an existing Canasta installation. This extracts
MediaWiki code for live editing, builds an Xdebug-enabled image, and restarts
the instance with dev mode compose files. Only supported with Docker Compose.`,
		Example: `  # Enable dev mode on an installation
  canasta devmode enable -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Printf("Enabling development mode on '%s'...\n", instance.Id)

			// Determine the base image: check .env for CANASTA_IMAGE, fall back to default
			baseImage := canasta.GetDefaultImage()
			envVars, envErr := canasta.GetEnvVariable(filepath.Join(instance.Path, ".env"))
			if envErr == nil {
				if img, ok := envVars["CANASTA_IMAGE"]; ok && img != "" {
					baseImage = img
				}
			}

			// Enable dev mode (extract code, create files, build xdebug image)
			if err := devmodePkg.EnableDevMode(instance.Path, *orch, baseImage); err != nil {
				return err
			}

			// Update config registry
			instance.DevMode = true
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

			fmt.Println("\033[32mDevelopment mode enabled. Edit files in mediawiki-code/ - changes appear immediately.\033[0m")
			fmt.Println("\033[32mVSCode: Open the installation directory, install PHP Debug extension, and start 'Listen for Xdebug'.\033[0m")
			return nil
		},
	}
}
