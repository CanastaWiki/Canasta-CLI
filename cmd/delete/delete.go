package start

import (
	"fmt"
	"log"
	"os"
	"os/exec"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

func NewCmdCreate() *cobra.Command {
	var instance logging.Installation

	var deleteCmd = &cobra.Command{
		Use:   "delete",
		Short: "delete a  Canasta installation",
		RunE: func(cmd *cobra.Command, args []string) error {

			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			err := Delete(instance)
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
	deleteCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	deleteCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Name of the Canasta Wiki Installation")
	return deleteCmd
}

func Delete(instance logging.Installation) error {
	fmt.Println("Deleting Canasta")
	var err error
	if instance.Id != "" {
		instance, err = logging.GetDetails(instance.Id)
		if err != nil {
			return err
		}
	} else {
		instance.Id, err = logging.GetCanastaId(instance.Path)
		if err != nil {
			return err
		}
	}

	err = exec.Command("rm", "-rf", instance.Path).Run()
	if err != nil {
		return err
	}
	err = logging.Delete(instance.Id)
	if err == nil {
		fmt.Println("Deleted Canasta")
	}
	return err
}
