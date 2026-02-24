package config

import (
	_ "embed"
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

//go:embed env-template.env
var envTemplate string

func generateCmdCreate() *cobra.Command {
	return &cobra.Command{
		Use:   "generate [output-file]",
		Short: "Generate a documented .env template",
		Long: `Generate a documented .env template with all available Canasta settings.

With no arguments, prints the template to stdout.
With an output file argument, writes the template to that file.`,
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 0 {
				fmt.Print(envTemplate)
				return nil
			}

			if err := os.WriteFile(args[0], []byte(envTemplate), 0644); err != nil {
				return fmt.Errorf("failed to write template: %w", err)
			}
			fmt.Printf("Template written to %s\n", args[0])
			return nil
		},
	}
}
