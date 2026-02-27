package cmd

import (
	addCmd "github.com/CanastaWiki/Canasta-CLI/cmd/add"
	configCmd "github.com/CanastaWiki/Canasta-CLI/cmd/config"
	createCmd "github.com/CanastaWiki/Canasta-CLI/cmd/create"
	deleteCmd "github.com/CanastaWiki/Canasta-CLI/cmd/delete"
	devmodeCmd "github.com/CanastaWiki/Canasta-CLI/cmd/devmode"
	exportCmd "github.com/CanastaWiki/Canasta-CLI/cmd/export"
	extensionCmd "github.com/CanastaWiki/Canasta-CLI/cmd/extension"
	importCmd "github.com/CanastaWiki/Canasta-CLI/cmd/import"
	listCmd "github.com/CanastaWiki/Canasta-CLI/cmd/list"
	maintenanceCmd "github.com/CanastaWiki/Canasta-CLI/cmd/maintenance"
	removeCmd "github.com/CanastaWiki/Canasta-CLI/cmd/remove"
	restartCmd "github.com/CanastaWiki/Canasta-CLI/cmd/restart"
	backupCmd "github.com/CanastaWiki/Canasta-CLI/cmd/backup"
	sitemapCmd "github.com/CanastaWiki/Canasta-CLI/cmd/sitemap"
	skinCmd "github.com/CanastaWiki/Canasta-CLI/cmd/skin"
	startCmd "github.com/CanastaWiki/Canasta-CLI/cmd/start"
	stopCmd "github.com/CanastaWiki/Canasta-CLI/cmd/stop"
	upgradeCmd "github.com/CanastaWiki/Canasta-CLI/cmd/upgrade"
	versionCmd "github.com/CanastaWiki/Canasta-CLI/cmd/version"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"

	"github.com/spf13/cobra"
)

var verbose bool

var rootCmd = &cobra.Command{
	Use:   "canasta",
	Short: "A CLI tool for Canasta installations.",
	Long: `Canasta CLI manages Canasta MediaWiki installations using Docker Compose
or Kubernetes. It supports creating, importing, starting, stopping, upgrading,
and backing up multiple Canasta instances, including wiki farms with multiple
wikis per instance.`,
	SilenceErrors: true,
	SilenceUsage:  true,
	PersistentPreRun: func(cmd *cobra.Command, args []string) {
		logging.SetVerbose(verbose)
		logging.Print("Verbose logging enabled")
	},
	Run: func(cmd *cobra.Command, args []string) {
		_ = cmd.Help()
	},
}

func Execute() {
	err := rootCmd.Execute()
	if err != nil {
		logging.Fatal(err)
	}
}

// GetRootCmd returns the root command for documentation generation
func GetRootCmd() *cobra.Command {
	return rootCmd
}

func init() {
	rootCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose output")

	rootCmd.AddCommand(configCmd.NewCmd())
	rootCmd.AddCommand(createCmd.NewCmd())
	rootCmd.AddCommand(deleteCmd.NewCmd())
	rootCmd.AddCommand(devmodeCmd.NewCmd())
	rootCmd.AddCommand(exportCmd.NewCmd())
	rootCmd.AddCommand(extensionCmd.NewCmd())
	rootCmd.AddCommand(importCmd.NewCmd())
	rootCmd.AddCommand(listCmd.NewCmd())
	rootCmd.AddCommand(maintenanceCmd.NewCmd())
	rootCmd.AddCommand(restartCmd.NewCmd())
	rootCmd.AddCommand(backupCmd.NewCmd())
	rootCmd.AddCommand(sitemapCmd.NewCmd())
	rootCmd.AddCommand(skinCmd.NewCmd())
	rootCmd.AddCommand(startCmd.NewCmd())
	rootCmd.AddCommand(stopCmd.NewCmd())
	rootCmd.AddCommand(upgradeCmd.NewCmd())
	rootCmd.AddCommand(versionCmd.NewCmd())
	rootCmd.AddCommand(addCmd.NewCmd())
	rootCmd.AddCommand(removeCmd.NewCmd())
	rootCmd.CompletionOptions.DisableDefaultCmd = true

	// Add config file location to help output
	defaultHelp := rootCmd.HelpFunc()
	rootCmd.SetHelpFunc(func(cmd *cobra.Command, args []string) {
		defaultHelp(cmd, args)
		if cmd == rootCmd {
			configDir, err := config.GetConfigDir()
			if err != nil {
				cmd.Printf("\nConfig file: (error: %s)\n", err)
			} else {
				cmd.Printf("\nConfig file: %s/conf.json\n", configDir)
			}
		}
	})
}
