package restic

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/spf13/cobra"
)

func forgetSnapshotCmdCreate() *cobra.Command {

	forgetSnapshotCmd := &cobra.Command{
		Use:   "forget",
		Short: "Forget restic snapshots",
		RunE: func(cmd *cobra.Command, args []string) error {
			if tag == "" && args[0] == "" {
				return fmt.Errorf("You must provide a restic snapshot tag")
			} else if tag == "" {
				tag = args[0]
			}
			forgetSnapshot()
			return nil
		},
	}

	forgetSnapshotCmd.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot ID (required)")
	return forgetSnapshotCmd
}

func forgetSnapshot() {
	commandArgs = append(commandArgs, "forget", tag)
	err, output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	} else {
		fmt.Print(output)
	}
}
