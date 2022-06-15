package stop

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var (
		path         string
		orchestrator string
	)

	var stopCmd = &cobra.Command{
		Use:   "stop",
		Short: "Stop the Canasta installation",
		Long:  `Stop the Canasta installation`,
		RunE: func(cmd *cobra.Command, args []string) error {
			err := Stop(path, orchestrator)
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
	stopCmd.Flags().StringVarP(&path, "path", "p", pwd, "Canasta installation directory")
	stopCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	return stopCmd
}

func Stop(path, orchestrator string) error {
	fmt.Println("Stopping Canasta")
	orchestrators.Stop(path, orchestrator)

	return nil
}
