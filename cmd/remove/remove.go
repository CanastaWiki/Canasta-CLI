package remove

import (
	"bufio"
	"fmt"
	"log"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/cmd/restart"
	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var wikiID string

	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	addCmd := &cobra.Command{
		Use:   "remove",
		Short: "Remove a wiki from a Canasta instance",
		RunE: func(cmd *cobra.Command, args []string) error {
			var err error
			fmt.Printf("Removing wiki '%s' from Canasta instance '%s'...\n", wikiID, instance.Id)
			err = RemoveWiki(instance, wikiID)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Done.")
			return nil
		},
	}

	addCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "ID of the wiki")
	addCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	return addCmd
}

// RemoveWiki removes a wiki with the given wikiID from a Canasta instance
func RemoveWiki(instance config.Installation, wikiID string) error {
	var err error
	//Checking Installation existence
	instance, err = canasta.CheckCanastaId(instance)
	if err != nil {
		return err
	}

	//Checking Running status
	err = orchestrators.CheckRunningStatus(instance)
	if err != nil {
		return err
	}

	//Checking Wiki existence
	exists, err := farmsettings.WikiIDExists(instance.Path, wikiID)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("A wiki with the ID '%s' does not exist", wikiID)
	}

	reader := bufio.NewReader(os.Stdin)
	fmt.Print("This will delete the wiki " + wikiID + " in the Canasta instance " + instance.Id + " and the corresponding database. Continue? [y/N] ")
	text, _ := reader.ReadString('\n')
	text = strings.ToLower(strings.TrimSpace(text))

	if text != "y" {
		fmt.Println("Operation cancelled.")
		return nil
	}

	//Remove the wiki
	err = farmsettings.RemoveWiki(wikiID, instance.Path)
	if err != nil {
		return err
	}

	//Install the corresponding Database
	err = mediawiki.RemoveDatabase(instance.Path, wikiID, instance.Orchestrator)
	if err != nil {
		return err
	}

	//Remove the Localsettings
	err = canasta.RemoveSettings(instance.Path, wikiID)
	if err != nil {
		return err
	}

	//Remove the Images (from inside container first to handle www-data ownership on Linux)
	orchestrators.CleanupWikiImages(instance.Path, instance.Orchestrator, wikiID)
	err = canasta.RemoveImages(instance.Path, wikiID)
	if err != nil {
		return err
	}

	//Rewrite the Caddyfile
	err = canasta.RewriteCaddy(instance.Path)
	if err != nil {
		return err
	}

	//Restart the Canasta Instance
	err = restart.Restart(instance, false, false)
	if err != nil {
		return err
	}

	fmt.Println("Successfully removed wiki " + wikiID + " from Canasta instance " + instance.Id + ".")

	return nil
}
