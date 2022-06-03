/*
Copyright © 2022 NAME HERE <EMAIL ADDRESS>

*/
package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var (
	Path              string
	Orchestrator      string
	DatabasePath      string
	LocalSettingsPath string
	EnvPath           string
	GithubLinks       = map[string]string{"docker-compose": "https://github.com/CanastaWiki/Canasta-DockerCompose/archive/refs/heads/main.zip"}
)

// createCmd represents the create command
var createCmd = &cobra.Command{
	Use:   "create",
	Short: "Create a Canasta Installation",
	Long:  `A Command to create a Canasta Installation with Docker-compose, Kubernetes, AWS. Also allows you to import from your previous installations.`,
	Run: func(cmd *cobra.Command, args []string) {
		fmt.Println("Initializing canasta at " + Path + "\nOrchestrator: " + Orchestrator)
	},
}

// func downloadCanasta() {
// 	gitDownload := exec.Command("git", GithubLinks[Orchestrator], "-P", Path)
// 	err := gitDownload.Run()
// 	if err != nil {
// 		log.Fatal(err)
// 	}
// }

func init() {
	createCmd.Flags().StringVarP(&Path, "path", "p", "", "Canasta Installation directory")
	createCmd.MarkFlagRequired("path")
	createCmd.Flags().StringVarP(&Orchestrator, "orchestrator", "o", "docker-compose", "Orchestrator to use for installation")
	createCmd.Flags().StringVarP(&DatabasePath, "database", "d", "", "Path to the existing database dump")
	createCmd.Flags().StringVarP(&LocalSettingsPath, "localsettings", "l", "", "Path to the existing LocalSettings.php")
	createCmd.Flags().StringVarP(&EnvPath, "env", "e", "", "Path to the existing .env file")
	rootCmd.AddCommand(createCmd)
}
