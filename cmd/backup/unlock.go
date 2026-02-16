package backup

import (
	"fmt"

	"github.com/spf13/cobra"
)

func unlockCmdCreate() *cobra.Command {

	unlockCmd := &cobra.Command{
		Use:   "unlock",
		Short: "Remove locks other processes created",
		Long: `Remove stale lock files from the backup repository. Use this if a previous
backup operation was interrupted and left the repository in a locked state.`,
		Example: `  canasta backup unlock -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return unlock()
		},
	}
	return unlockCmd
}

func unlock() error {
	output, err := runRestic(nil, "-r", repoURL, "unlock")
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
