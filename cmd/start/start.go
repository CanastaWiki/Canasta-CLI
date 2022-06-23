package start

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

	var startCmd = &cobra.Command{
		Use:   "start",
		Short: "Start the Canasta installation",
		Long:  `Start the Canasta installation`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" {
				instance.Id = args[0]
			}
			err := Start(instance)
			if err != nil {
				return err
			}
			return nil
		},
	}

	// Defaults the path's value to the current working directory if no value is passed
	pwd, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	startCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	startCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Name of the Canasta Wiki Installation")
	startCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	return startCmd
}

func Start(instance logging.Installation) error {
	fmt.Println("Starting Canasta")
	var err error
	if instance.Id != "" {
		instance, err = logging.GetDetails(instance.Id)
		if err != nil {
			return err
		}
	}
	err = orchestrators.Start(instance.Path, instance.Orchestrator)
	return err
}
