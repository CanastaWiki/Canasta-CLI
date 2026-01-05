package cmd

import (
	"fmt"
	"path/filepath"

	addCmd "github.com/CanastaWiki/Canasta-CLI/cmd/add"
	createCmd "github.com/CanastaWiki/Canasta-CLI/cmd/create"
	deleteCmd "github.com/CanastaWiki/Canasta-CLI/cmd/delete"
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

var (
	verbose          bool
	OrchestratorPath string
)

var rootCmd = &cobra.Command{
	Use:   "canasta",
	Short: "A CLI tool for Canasta installations.",
	Long:  `A CLI tool to create, import, start, stop and backup multiple Canasta installations`,
	PersistentPreRun: func(cmd *cobra.Command, args []string) {
		logging.SetVerbose(verbose)
		logging.Print("Setting verbose")
	},
	Run: func(cmd *cobra.Command, args []string) {
		if OrchestratorPath != "" {
			OrchestratorPath, err := filepath.Abs(OrchestratorPath)
			if err != nil {
				logging.Fatal(err)
			}
			var orchestrator = config.Orchestrator{
				Id:   "compose",
				Path: OrchestratorPath}
			err = config.AddOrchestrator(orchestrator)
			if err != nil {
				logging.Fatal(err)
			}
			fmt.Printf("Path to Orchestrator %s set to %s", orchestrator.Id, orchestrator.Path)
		}

	},
}

func Execute() {
	err := rootCmd.Execute()
	if err != nil {
		logging.Fatal(err)
	}
}

func init() {
	rootCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Verbose output")
	rootCmd.Flags().StringVarP(&OrchestratorPath, "docker-path", "d", "", "path to docker")

	rootCmd.AddCommand(createCmd.NewCmdCreate())
	rootCmd.AddCommand(deleteCmd.NewCmdCreate())
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

	cobra.OnInitialize(func() {
		if OrchestratorPath == "" {
			orchestrators.CheckDependencies()
		}

	})
}
