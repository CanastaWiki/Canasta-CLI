package maintenance

import (
	"fmt"
	"log"
	"os"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func scriptCmdCreate() *cobra.Command {

	scriptCmd := &cobra.Command{
		Use:   `script "[scriptname.php] [args]"`,
		Short: "Run maintenance scripts",
		Args:  cobra.ExactArgs(1),
		PreRunE: func(cmd *cobra.Command, args []string) error {
			instance, err = canasta.CheckCanastaId(instance)
			return err
		},
		Run: func(cmd *cobra.Command, args []string) {
			logging.SetVerbose(true)
			runMaintenanceScript(instance, args[0])
		},
	}

	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}
	return scriptCmd
}

func runMaintenanceScript(instance config.Installation, script string) {
	fmt.Println("Running maintenance script " + script)
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "php maintenance/"+script)
	fmt.Println("Completed running maintenance script")

}
