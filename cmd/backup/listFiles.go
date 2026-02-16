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
			return listFiles()
		},
	}
	filesCmd.Flags().StringVarP(&tag, "tag", "t", "", "Snapshot ID (required)")
	_ = filesCmd.MarkFlagRequired("tag")
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
