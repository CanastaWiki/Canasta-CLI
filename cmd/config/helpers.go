package config

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// resolveKey finds the actual key in envVars using case-insensitive matching
// with hyphens treated as underscores. If no match is found, returns the
// input uppercased with hyphens replaced by underscores.
func resolveKey(envVars map[string]string, input string) string {
	normalized := strings.ReplaceAll(input, "-", "_")
	for k := range envVars {
		if strings.EqualFold(k, normalized) {
			return k
		}
	}
	return strings.ToUpper(normalized)
}

// isKnownKey reports whether key is in the knownKeys set or matches a
// Restic backend prefix.
func isKnownKey(key string) bool {
	if knownKeys[key] {
		return true
	}
	for _, prefix := range resticPrefixes {
		if strings.HasPrefix(key, prefix) {
			return true
		}
	}
	return false
}

// restartInstance performs the UpdateConfig → Stop → optional kind cluster
// recreation → Start sequence shared by config set and config unset.
func restartInstance(orch *orchestrators.Orchestrator, instance config.Instance, portKeyChanged bool) error {
	fmt.Println("Applying configuration and restarting...")
	if err := (*orch).UpdateConfig(instance.Path); err != nil {
		return fmt.Errorf("failed to update config: %w", err)
	}
	if err := (*orch).Stop(instance); err != nil {
		return fmt.Errorf("failed to stop instance: %w", err)
	}
	if instance.KindCluster != "" && portKeyChanged {
		if err := recreateKindCluster(instance); err != nil {
			return err
		}
	}
	if err := (*orch).Start(instance); err != nil {
		return fmt.Errorf("failed to start instance: %w", err)
	}
	fmt.Println("Done.")
	return nil
}
