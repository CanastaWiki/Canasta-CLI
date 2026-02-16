package backup

import (
	"fmt"

	"github.com/spf13/cobra"
)

func filesCmdCreate() *cobra.Command {

	filesCmd := &cobra.Command{
		Use:   "files",
		Short: "List files in a backup",
		Long: `List all files contained in a specific backup snapshot. This is useful
for inspecting what was backed up before performing a restore.`,
		Example: `  canasta backup files -i myinstance -t abc123`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if tag == "" && args[0] == "" {
				return fmt.Errorf("You must provide a snapshot ID")
			} else if tag == "" {
				tag = args[0]
			}
			return listFiles()
		},
	}
	filesCmd.Flags().StringVarP(&tag, "tag", "t", "", "Snapshot ID (required)")
	return filesCmd
}

func listFiles() error {
	output, err := runBackup(nil, "-r", repoURL, "ls", tag)
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
