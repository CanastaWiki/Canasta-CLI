package config

import (
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
)

func getCmdCreate() *cobra.Command {
	return &cobra.Command{
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
}

// resolveKey finds the actual key in envVars using case-insensitive matching.
// If no match is found, returns the input uppercased.
func resolveKey(envVars map[string]string, input string) string {
	for k := range envVars {
		if strings.EqualFold(k, input) {
			return k
		}
	}
	return strings.ToUpper(input)
}
