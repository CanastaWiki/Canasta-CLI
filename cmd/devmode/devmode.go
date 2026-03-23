package devmode

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmd() *cobra.Command {
	var (
		instance config.Instance
		orch     orchestrators.Orchestrator
	)

	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		logging.Fatal(wdErr)
	}
	instance.Path = workingDir

	devmodeCmd := &cobra.Command{
		Use:   "devmode",
		Short: "Manage development mode",
		Long: `Enable or disable development mode on a Canasta instance. Development
mode extracts MediaWiki code for live editing and enables Xdebug for step
debugging. This is only supported with Docker Compose.`,
		PersistentPreRunE: func(_ *cobra.Command, _ []string) error {
			var err error
			instance, err = canasta.CheckCanastaID(instance)
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

	devmodeCmd.PersistentFlags().StringVarP(&instance.ID, "id", "i", "", "Canasta instance ID (defaults to instance associated with current directory)")

	devmodeCmd.AddCommand(newEnableCmd(&instance, &orch))
	devmodeCmd.AddCommand(newDisableCmd(&instance, &orch))

	return devmodeCmd
}
