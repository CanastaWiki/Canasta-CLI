package restic

import (
	"fmt"

	"github.com/spf13/cobra"
)

var (
	tag1, tag2 string
)

func diffCmdCreate() *cobra.Command {

	diffCmd := &cobra.Command{
		Use:   "diff",
		Short: "Show difference between two snapshots",
		Long: `Show the differences between two Restic snapshots. This compares the file
contents and metadata of both snapshots, displaying added, removed, and
modified files.`,
		Example: `  canasta restic diff -i myinstance --tag1 abc123 --tag2 def456`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return diff()
		},
	}
	diffCmd.Flags().StringVar(&tag1, "tag1", "", "Restic snapshot ID (required)")
	diffCmd.Flags().StringVar(&tag2, "tag2", "", "Restic snapshot ID (required)")
	_ = diffCmd.MarkFlagRequired("tag1")
	_ = diffCmd.MarkFlagRequired("tag2")
	return diffCmd
}

func diff() error {
	output, err := runRestic(nil, "-r", repoURL, "diff", tag1, tag2)
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
