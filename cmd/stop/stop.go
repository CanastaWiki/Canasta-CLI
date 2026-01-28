package stop

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	var stopCmd = &cobra.Command{
		Use:   "stop",
		Short: "Shuts down the Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			resolvedInstance, err := canasta.CheckCanastaId(instance)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Stopping Canasta installation '" + resolvedInstance.Id + "'...")
			err = Stop(resolvedInstance)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Stopped.")
			return nil
		},
	}
	stopCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	return stopCmd
}

func Stop(instance config.Installation) error {
	// orchestrators.Stop handles dev mode automatically
	return orchestrators.Stop(instance)
}
