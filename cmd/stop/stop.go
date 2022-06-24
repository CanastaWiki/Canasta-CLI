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

	var stopCmd = &cobra.Command{
		Use:   "stop",
		Short: "Stop the Canasta installation",
		Long:  `Stop the Canasta installation`,
		RunE: func(cmd *cobra.Command, args []string) error {

			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			err := Stop(instance)
			if err != nil {
				log.Fatal(err)
			}
			return nil
		},
	}

	// Defaults the path's value to the current working directory if no value is passed
	pwd, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	stopCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	stopCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Name of the Canasta Wiki Installation")
	stopCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	return stopCmd
}

func Stop(instance logging.Installation) error {
	fmt.Println("Stopping Canasta")
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
