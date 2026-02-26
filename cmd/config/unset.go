package config

import (
	"fmt"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newUnsetCmd(instance *config.Installation, orch *orchestrators.Orchestrator) *cobra.Command {
	var force bool
	var noRestart bool
	cmd := &cobra.Command{
		Use:   "unset KEY [KEY ...]",
		Short: "Remove a configuration setting",
		Long: `Remove one or more configuration keys from the .env file of a Canasta installation.

Each argument is a key name to remove. Multiple keys can be removed in a single
invocation and only one restart is performed.

If a key has side effects (e.g., HTTPS_PORT), they are reverted before the key
is removed. The instance is then restarted unless --no-restart is specified.`,
		Example: `  canasta config unset CADDY_AUTO_HTTPS -i myinstance
  canasta config unset HTTPS_PORT -i myinstance
  canasta config unset HTTP_PORT HTTPS_PORT -i myinstance
  canasta config unset CANASTA_ENABLE_OBSERVABILITY -i myinstance --no-restart`,
		Args: cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			envPath := filepath.Join(instance.Path, ".env")
			envVars, err := canasta.GetEnvVariable(envPath)
			if err != nil {
				return fmt.Errorf("failed to read .env: %w", err)
			}

			// Resolve and validate all keys exist
			keys := make([]string, 0, len(args))
			for _, arg := range args {
				key := resolveKey(envVars, arg)
				if !force && !isKnownKey(key) {
					return fmt.Errorf("unrecognized setting %q\nUse 'canasta config unset --force %s' to remove it anyway\nRun 'canasta config --help' to see available settings", key, key)
				}
				if _, ok := envVars[key]; !ok {
					return fmt.Errorf("key %q is not set", key)
				}
				keys = append(keys, key)
			}

			// Run unapply side effects before removing keys
			for _, key := range keys {
				if se, ok := sideEffects[key]; ok && se.unapply != nil {
					if err := se.unapply(*instance); err != nil {
						return fmt.Errorf("unapply side effect for %s failed: %w", key, err)
					}
				}
			}

			// Remove keys from .env
			portKeyChanged := false
			for _, key := range keys {
				if err := canasta.DeleteEnvVariable(envPath, key); err != nil {
					return fmt.Errorf("failed to remove %s: %w", key, err)
				}
				logging.Print(fmt.Sprintf("Removed %s\n", key))
				if portKeys[key] {
					portKeyChanged = true
				}
			}

			if noRestart {
				fmt.Println("Settings removed. Restart skipped (--no-restart).")
				return nil
			}

			// Restart: UpdateConfig → Stop → (recreate kind cluster if port key) → Start
			fmt.Println("Applying configuration and restarting...")
			if err := (*orch).UpdateConfig(instance.Path); err != nil {
				return fmt.Errorf("failed to update config: %w", err)
			}
			if err := (*orch).Stop(*instance); err != nil {
				return fmt.Errorf("failed to stop instance: %w", err)
			}
			if instance.KindCluster != "" && portKeyChanged {
				if err := recreateKindCluster(*instance); err != nil {
					return err
				}
			}
			if err := (*orch).Start(*instance); err != nil {
				return fmt.Errorf("failed to start instance: %w", err)
			}
			fmt.Println("Done.")
			return nil
		},
	}

	cmd.Flags().BoolVar(&noRestart, "no-restart", false, "Remove the setting without restarting the instance")
	cmd.Flags().BoolVarP(&force, "force", "f", false, "Allow unsetting unrecognized keys")
	return cmd
}
