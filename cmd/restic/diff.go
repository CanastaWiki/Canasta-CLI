package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
)

var (
	tag1, tag2 string
)

func diffCmdCreate() *cobra.Command {

	diffCmd := &cobra.Command{
		Use:   "diff",
		Short: "Show difference between two snapshots",
		RunE: func(cmd *cobra.Command, args []string) error {
			diff()
			return nil
		},
	}
	diffCmd.Flags().StringVar(&tag1, "tag1", "", "Restic snapshot ID (required)")
	diffCmd.Flags().StringVar(&tag2, "tag2", "", "Restic snapshot ID (required)")
	diffCmd.MarkFlagRequired("tag1")
	diffCmd.MarkFlagRequired("tag2")
	return diffCmd
}

func diff() {
	commandArgs = append(commandArgs, "diff", tag1, tag2)
	output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	fmt.Print(output)
}
