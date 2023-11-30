package start

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

var (
	instance config.Installation
	pwd      string
	err      error
)

func NewCmdCreate() *cobra.Command {
	var startCmd = &cobra.Command{
		Use:   "start",
		Short: "Start the Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			fmt.Println("Starting Canasta installation '" + instance.Id + "'...")
			if err := Start(instance); err != nil {
				logging.Fatal(err)
			}
			fmt.Println("Started.")
			return nil
		},
	}
	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}
	startCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	startCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	startCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "compose", "Orchestrator to use for installation")
	return startCmd
}

func Start(instance config.Installation) error {
	if instance.Id != "" {
		if instance, err = config.GetDetails(instance.Id); err != nil {
			logging.Fatal(err)
		}
	}
	if err = orchestrators.Start(instance.Path, instance.Orchestrator); err != nil {
		logging.Fatal(err)
	}
	return err
}
