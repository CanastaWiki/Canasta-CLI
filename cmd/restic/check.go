package restic

import (
	"fmt"

	"github.com/spf13/cobra"
)

func checkCmdCreate() *cobra.Command {

	checkCmd := &cobra.Command{
		Use:   "check",
		Short: "Check restic snapshots",
		Long: `Verify the integrity of the Restic backup repository and its data. This
checks for errors in the repository structure and snapshot data.`,
		Example: `  canasta restic check -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return check()
		},
	}
	checkCmd.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot ID (required)")
	return checkCmd
}

func check() error {
	output, err := runRestic(nil, "-r", repoURL, "check")
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
