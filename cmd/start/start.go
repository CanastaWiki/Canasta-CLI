package start

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

	var startCmd = &cobra.Command{
		Use:   "start",
		Short: "Start the Canasta installation",
		Long:  `Start the Canasta installation`,
		RunE: func(cmd *cobra.Command, args []string) error {
			err := Start(path, orchestrator)
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
	startCmd.Flags().StringVarP(&path, "path", "p", pwd, "Canasta installation directory")
	startCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	return startCmd
}

func Start(path, orchestrator string) error {
	fmt.Println("Startping Canasta")
	orchestrators.Start(path, orchestrator)

	return nil
}
