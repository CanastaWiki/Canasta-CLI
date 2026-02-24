package devmode

import (
	"fmt"
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
	err      error
	verbose  bool
)

func NewCmdCreate() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	devmodeCmd := &cobra.Command{
		Use:   "devmode",
		Short: "Manage development mode",
		Long: `Enable or disable development mode on a Canasta installation. Development
mode extracts MediaWiki code for live editing and enables Xdebug for step
debugging. This is only supported with Docker Compose.`,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			orch, err = orchestrators.NewFromInstance(instance)
			if err != nil {
				return err
			}
			if !orch.SupportsDevMode() {
				return fmt.Errorf("development mode is not supported with %s", orch.Name())
			}
			return nil
		},
	}

	devmodeCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	devmodeCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")

	devmodeCmd.AddCommand(enableCmdCreate())
	devmodeCmd.AddCommand(disableCmdCreate())

	return devmodeCmd
}
