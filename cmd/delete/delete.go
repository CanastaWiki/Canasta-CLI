package start

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

var (
	pwd      string
	verbose  bool
	err      error
	instance logging.Installation
)

func NewCmdCreate() *cobra.Command {
	logging.SetVerbose(verbose)
	var deleteCmd = &cobra.Command{
		Use:   "delete",
		Short: "Delete a  Canasta installation",
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
	deleteCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose output")
	return deleteCmd
}

func Delete(instance logging.Installation) error {
	fmt.Println("Deleting Canasta")
	var err error

	//Checking Installation existence
	if instance.Id != "" {
		if instance, err = logging.GetDetails(instance.Id); err != nil {
			return err
		}
	} else {
		if instance.Id, err = logging.GetCanastaId(instance.Path); err != nil {
			return err
		}
		if instance, err = logging.GetDetails(instance.Id); err != nil {
			return err
		}
	}

	//Stopping and deleting Contianers and it's volumes
	orchestrators.Delete(instance.Path, instance.Orchestrator)

	//Deleting installation details from conf.json
	if err = logging.Delete(instance.Id); err != nil {
		return err
	}
	fmt.Println("Deleted Canasta")
	return nil
}
