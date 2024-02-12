package start

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/spinner"
)

var (
	pwd      string
	err      error
	instance config.Installation
)

func NewCmdCreate() *cobra.Command {
	var deleteCmd = &cobra.Command{
		Use:   "delete",
		Short: "Delete a Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			if err := Delete(instance); err != nil {
				return err
			}
			return nil
		},
	}
	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}
	deleteCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	deleteCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	return deleteCmd
}

func Delete(instance config.Installation) error {
	description := "Deleting Canasta installation '" + instance.Id + "'..."
	_, done := spinner.New(description)
	defer func() {
		done <- struct{}{}
	}()
	var err error

	//Checking Installation existence
	instance, err = canasta.CheckCanastaId(instance)
	if err != nil {
		return err
	}

	//Stopping and deleting Contianers and it's volumes
	if _, err := orchestrators.DeleteContainers(instance.Path, instance.Orchestrator); err != nil {
		return err
	}

	//Delete config files
	if _, err := orchestrators.DeleteConfig(instance.Path); err != nil {
		return err
	}

	//Deleting installation details from conf.json
	if err = config.Delete(instance.Id); err != nil {
		return err
	}

	fmt.Println("Deleted.")
	return nil
}
