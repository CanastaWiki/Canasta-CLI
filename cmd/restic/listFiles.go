package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

func listFilesCmdCreate() *cobra.Command {

	listFilesCmd := &cobra.Command{
		Use:   "list",
		Short: "List files in a snapshot",
		Long: `List all files contained in a specific Restic snapshot. This is useful
for inspecting what was backed up before performing a restore.`,
		Example: `  canasta restic list -i myinstance -t abc123`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if tag == "" && args[0] == "" {
				return fmt.Errorf("You must provide a restic snapshot tag")
			} else if tag == "" {
				tag = args[0]
			}
			listFiles()
			return nil
		},
	}
	listFilesCmd.Flags().StringVarP(&tag, "tag", "t", "", "Restic snapshot ID (required)")
	return listFilesCmd
}

func listFiles() {
	commandArgs = append(commandArgs, "ls", tag)
	err, output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	} else {
		fmt.Print(output)
	}
}
