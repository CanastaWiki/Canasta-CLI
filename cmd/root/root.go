package cmd

import (
	addCmd "github.com/CanastaWiki/Canasta-CLI/cmd/add"
	createCmd "github.com/CanastaWiki/Canasta-CLI/cmd/create"
	deleteCmd "github.com/CanastaWiki/Canasta-CLI/cmd/delete"
	exportCmd "github.com/CanastaWiki/Canasta-CLI/cmd/export"
	extensionCmd "github.com/CanastaWiki/Canasta-CLI/cmd/extension"
	importCmd "github.com/CanastaWiki/Canasta-CLI/cmd/import"
	listCmd "github.com/CanastaWiki/Canasta-CLI/cmd/list"
	maintenanceCmd "github.com/CanastaWiki/Canasta-CLI/cmd/maintenanceUpdate"
	removeCmd "github.com/CanastaWiki/Canasta-CLI/cmd/remove"
	restartCmd "github.com/CanastaWiki/Canasta-CLI/cmd/restart"
	resticCmd "github.com/CanastaWiki/Canasta-CLI/cmd/restic"
	skinCmd "github.com/CanastaWiki/Canasta-CLI/cmd/skin"
	startCmd "github.com/CanastaWiki/Canasta-CLI/cmd/start"
	stopCmd "github.com/CanastaWiki/Canasta-CLI/cmd/stop"
	upgradeCmd "github.com/CanastaWiki/Canasta-CLI/cmd/upgrade"
	versionCmd "github.com/CanastaWiki/Canasta-CLI/cmd/version"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"

	"github.com/spf13/cobra"
)

var verbose bool

var rootCmd = &cobra.Command{
	Use:   "canasta",
	Short: "A CLI tool for Canasta installations.",
	Long: `Canasta CLI manages Canasta MediaWiki installations using Docker Compose.
It supports creating, importing, starting, stopping, upgrading, and backing up
multiple Canasta instances, including wiki farms with multiple wikis per instance.`,
	PersistentPreRun: func(cmd *cobra.Command, args []string) {
		logging.SetVerbose(verbose)
		logging.Print("Setting verbose")
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

	rootCmd.AddCommand(createCmd.NewCmdCreate())
	rootCmd.AddCommand(deleteCmd.NewCmdCreate())
	rootCmd.AddCommand(exportCmd.NewCmdCreate())
	rootCmd.AddCommand(extensionCmd.NewCmdCreate())
	rootCmd.AddCommand(importCmd.NewCmdCreate())
	rootCmd.AddCommand(listCmd.NewCmdCreate())
	rootCmd.AddCommand(maintenanceCmd.NewCmdCreate())
	rootCmd.AddCommand(restartCmd.NewCmdCreate())
	rootCmd.AddCommand(resticCmd.NewCmdCreate())
	rootCmd.AddCommand(skinCmd.NewCmdCreate())
	rootCmd.AddCommand(startCmd.NewCmdCreate())
	rootCmd.AddCommand(stopCmd.NewCmdCreate())
	rootCmd.AddCommand(upgradeCmd.NewCmdCreate())
	rootCmd.AddCommand(versionCmd.NewCmdCreate())
	rootCmd.AddCommand(addCmd.NewCmdCreate())
	rootCmd.AddCommand(removeCmd.NewCmdCreate())
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

	cobra.OnInitialize(func() {
		orch, err := orchestrators.New("compose")
		if err != nil {
			logging.Fatal(err)
		}
		if err := orch.CheckDependencies(); err != nil {
			logging.Fatal(err)
		}
	})
}
