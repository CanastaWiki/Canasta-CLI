package config

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var (
	instance config.Installation
	orch     orchestrators.Orchestrator
	verbose  bool
	err      error
)

func NewCmdCreate() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	configCmd := &cobra.Command{
		Use:   "config",
		Short: "View and modify Canasta configuration",
		Long: `View and modify the .env configuration for a Canasta installation.
Subcommands allow you to get or set individual settings, or generate
a documented .env template.`,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			// generate doesn't need an instance
			if cmd.Name() == "generate" {
				return nil
			}
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			orch, err = orchestrators.NewFromInstance(instance)
			return err
		},
	}

	configCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	configCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose output")

	configCmd.AddCommand(getCmdCreate())
	configCmd.AddCommand(setCmdCreate())
	configCmd.AddCommand(generateCmdCreate())

	return configCmd
}
