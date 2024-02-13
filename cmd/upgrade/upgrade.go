package upgrade

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/git"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

var (
	pwd      string
	err      error
	instance config.Installation
)

func NewCmdCreate() *cobra.Command {
	var upgradeCmd = &cobra.Command{
		Use:   "upgrade",
		Short: "Upgrade a Canasta installation to the latest version",
		RunE: func(cmd *cobra.Command, args []string) error {
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			if err := Upgrade(instance); err != nil {
				return err
			}
			return nil
		},
	}
	if pwd, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}

	upgradeCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Canasta installation directory")
	upgradeCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	return upgradeCmd
}

func Upgrade(instance config.Installation) error {

	var err error

	//Checking Installation existence
	instance, err = canasta.CheckCanastaId(instance)
	if err != nil {
		return err
	}
	fmt.Print("Pulling the latest changes\n")
	//Pulling the latest changes from github
	if err = git.Pull(instance.Path); err != nil {
		return err
	}

	//Restarting the containers
	if err = orchestrators.StopAndStart(instance.Path, instance.Orchestrator); err != nil {
		return err
	}

	//Touch LocalSettings.php
	fmt.Print("Running 'touch LocalSettings.php' to flush cache\n")
	_, err = orchestrators.ExecWithError(instance.Path, instance.Orchestrator, "web", "touch LocalSettings.php")
	if err != nil {
		return err
	}
	fmt.Print("Canasta Upgraded!\n")
	return nil
}
