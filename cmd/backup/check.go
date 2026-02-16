package backup

import (
	"fmt"

	"github.com/spf13/cobra"
)

func checkCmdCreate() *cobra.Command {

	checkCmd := &cobra.Command{
		Use:   "check",
		Short: "Check backup repository integrity",
		Long: `Verify the integrity of the backup repository and its data. This
checks for errors in the repository structure and snapshot data.`,
		Example: `  canasta backup check -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return check()
		},
	}
	return checkCmd
}

func check() error {
	output, err := runBackup(nil, "-r", repoURL, "check")
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
