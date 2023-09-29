package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
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
	err, output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	} else {
		fmt.Print(output)
	}
}
