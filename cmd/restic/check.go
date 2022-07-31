package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
)

func checkCmdCreate() *cobra.Command {

	checkCmd := &cobra.Command{
		Use:   "check",
		Short: "Check restic snapshots",
		Run: func(cmd *cobra.Command, args []string) {
			check()
		},
	}
	checkCmd.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot ID (required)")
	return checkCmd
}

func check() {
	commandArgs = append(commandArgs, "check")
	output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	fmt.Print(output)
}
