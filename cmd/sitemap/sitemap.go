package sitemap

import (
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

var (
	instance   config.Installation
	orch       orchestrators.Orchestrator
	sitemapCmd *cobra.Command
)

func NewCmdCreate() *cobra.Command {
	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		log.Fatal(wdErr)
	}
	instance.Path = workingDir

	sitemapCmd = &cobra.Command{
		Use:   "sitemap",
		Short: "Manage sitemaps for a Canasta instance",
		Long: `Generate or remove XML sitemaps for wikis in a Canasta installation.
Sitemaps improve search engine indexing by listing all pages on your wiki.
Use "canasta sitemap generate" to create sitemaps and "canasta sitemap remove"
to delete them.`,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			var err error
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			orch, err = orchestrators.New(instance.Orchestrator)
			if err != nil {
				return err
			}
			return nil
		},
	}

	sitemapCmd.AddCommand(generateCmdCreate())
	sitemapCmd.AddCommand(removeCmdCreate())

	sitemapCmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")

	return sitemapCmd
}
