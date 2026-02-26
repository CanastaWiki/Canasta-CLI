package config

import (
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
)

func newGetCmd(instance *config.Installation) *cobra.Command {
	var force bool
	cmd := &cobra.Command{
		Use:   "get [KEY]",
		Short: "Show configuration settings",
		Long: `Show configuration settings from the .env file of a Canasta installation.

With no arguments, prints all settings sorted alphabetically as KEY=VALUE lines.
With a KEY argument, prints just the value (no KEY= prefix) for easy scripting.
Key lookup is case-insensitive.`,
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			envPath := filepath.Join(instance.Path, ".env")
			envVars, err := canasta.GetEnvVariable(envPath)
			if err != nil {
				return fmt.Errorf("failed to read .env: %w", err)
			}

			if len(args) == 1 {
				key := resolveKey(envVars, args[0])
				if !force && !isKnownKey(key) {
					return fmt.Errorf("unrecognized setting %q\nUse 'canasta config get --force %s' to query it anyway\nRun 'canasta config --help' to see available settings", key, key)
				}
				val, ok := envVars[key]
				if !ok {
					return fmt.Errorf("key %q is not set", key)
				}
				fmt.Println(val)
				return nil
			}

			// Print all settings sorted
			keys := make([]string, 0, len(envVars))
			for k := range envVars {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				fmt.Printf("%s=%s\n", k, envVars[k])
			}
			return nil
		},
	}

	cmd.Flags().BoolVarP(&force, "force", "f", false, "Allow querying unrecognized keys")
	return cmd
}

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
