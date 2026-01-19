package stop

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/devmode"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var stopCmd = &cobra.Command{
		Use:   "stop",
		Short: "Shuts down the Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			fmt.Println("Stopping Canasta installation '" + instance.Id + "'...")
			err := Stop(instance)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Stopped.")
			return nil
		},
	}
	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	stopCmd.Flags().StringVarP(&instance.Path, "path", "p", workingDir, "Canasta installation directory")
	stopCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	stopCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "compose", "Orchestrator to use for installation")
	return stopCmd
}

func Stop(instance config.Installation) error {
	var err error
	if instance.Id != "" {
		instance, err = config.GetDetails(instance.Id)
		if err != nil {
			return err
		}
	}
	if instance.DevMode {
		err = devmode.StopDev(instance.Path, instance.Orchestrator)
	} else {
		err = orchestrators.Stop(instance.Path, instance.Orchestrator)
	}
	return err
}
