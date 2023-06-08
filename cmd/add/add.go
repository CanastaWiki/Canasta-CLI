package add

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/cmd/start"
	"github.com/CanastaWiki/Canasta-CLI-Go/cmd/stop"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/yaml"
)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var wikiName string
	var domainName string
	var wikiPath string

	addCmd := &cobra.Command{
		Use:   "add",
		Short: "Add a new wiki to a Canasta instance",
		RunE: func(cmd *cobra.Command, args []string) error {
			var err error
			wikiName, domainName, wikiPath, instance.Id, err = mediawiki.PromptWiki(wikiName, domainName, wikiPath, instance.Id)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Printf("Adding wiki '%s' to Canasta instance '%s'...\n", wikiName, instance.Id)
			err = AddWiki(wikiName, domainName, wikiPath, instance)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Done.")
			return nil
		},
	}

	pwd, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}

	addCmd.Flags().StringVarP(&wikiName, "wiki", "w", "", "Name of the new wiki")
	addCmd.Flags().StringVarP(&domainName, "domain-name", "n", "", "Domain name of the new wiki")
	addCmd.Flags().StringVarP(&instance.Path, "path", "p", pwd, "Path to the new wiki")
	addCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	addCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	addCmd.Flags().StringVarP(&wikiPath, "wiki-path", "d", "", "Directory path of the new wiki")
	return addCmd
}

// addWiki accepts the Canasta instance ID, the name, domain and path of the new wiki, and the initial admin info, then creates a new wiki in the instance.
func AddWiki(name, domain, wikipath string, instance config.Installation) error {
	var err error
	//Checking Installation existence
	instance, err = canasta.CheckCanastaId(instance)
	if err != nil {
		return err
	}

	//Checking Installation existence
	err = orchestrators.CheckRunningStatus(instance.Path, instance.Id)
	if err != nil {
		return err
	}

	//Checking Wiki existence
	err = yaml.CheckWiki(instance.Path, name)
	if err != nil {
		return err
	}

	//Add the wiki
	err = yaml.AddWiki(name, instance.Path, domain, wikipath)
	if err != nil {
		return err
	}

	//Stop the Canasta Instance
	err = stop.Stop(instance)
	if err != nil {
		return err
	}

	//Copy the Localsettings
	err = canasta.CopySetting(instance.Path, name)
	if err != nil {
		return err
	}

	//Rewrite the Caddyfile
	err = canasta.RewriteCaddy(instance.Path)
	if err != nil {
		return err
	}

	//Start the Canasta Instance
	err = start.Start(instance)
	if err != nil {
		return err
	}

	// //Install the corresponding Database
	err = mediawiki.InstallOne(instance.Path, name, domain, wikipath, instance.Orchestrator)
	if err != nil {
		return err
	}
	
	fmt.Println("Successfully Added the Wiki '" + name + "in Canasta instance '" + instance.Id + "'...")

	return nil
}
