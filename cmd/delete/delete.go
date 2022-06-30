package start

import (
	"fmt"
	"log"
	"os"
	"os/exec"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

var verbose bool

func NewCmdCreate() *cobra.Command {
	var instance logging.Installation
	logging.SetVerbose(verbose)
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
	pwd, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	deleteCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	deleteCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Name of the Canasta Wiki Installation")
	deleteCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
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

	if err = orchestrators.Delete(instance.Path, instance.Orchestrator); err != nil {
		return err
	}
	//Deleting the installation folder
	if err = exec.Command("rm", "-rf", instance.Path).Run(); err != nil {
		return err
	}
	if err = logging.Delete(instance.Id); err != nil {
		return err
	}
	fmt.Println("Deleted Canasta")
	return nil
}
