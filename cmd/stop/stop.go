package stop

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var instance logging.Installation
	var verbose bool
	var stopCmd = &cobra.Command{
		Use:   "stop",
		Short: "Shuts down the Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			fmt.Println("Stopping Canasta")
			err := Stop(instance)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Stopped")
			return nil
		},
	}
	pwd, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	stopCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	stopCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	stopCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	stopCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	return stopCmd
}

func Stop(instance logging.Installation) error {
	var err error
	if instance.Id != "" {
		instance, err = logging.GetDetails(instance.Id)
		if err != nil {
			return err
		}
	}
	err = orchestrators.Stop(instance.Path, instance.Orchestrator)
	return err
}
