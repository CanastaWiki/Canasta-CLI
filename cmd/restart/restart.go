package restart

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var verbose bool
	var restartCmd = &cobra.Command{
		Use:   "restart",
		Short: "Restart the Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			fmt.Println("Restarting Canasta installation '" + instance.Id + "'...")
			err := Restart(instance)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Restarted.")
			return nil
		},
	}
	pwd, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	restartCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	restartCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	restartCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	restartCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	return restartCmd
}

func Restart(instance config.Installation) error {
	var err error
	if instance.Id != "" {
		instance, err = config.GetDetails(instance.Id)
		if err != nil {
			return err
		}
	}
	err = orchestrators.StopAndStart(instance.Path, instance.Orchestrator)
	return err
}
