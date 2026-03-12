package gitops

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
)

func newAddCmd(instance *config.Installation) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "add [files...]",
		Short: "Stage files for the next gitops push",
		Long: `Explicitly stage files to be included in the next gitops push.
Only staged files will be committed when you run gitops push.`,
		Args: cobra.MinimumNArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			return runAdd(instance.Path, args)
		},
	}
	return cmd
}

func runAdd(installPath string, files []string) error {
	if err := gitops.Add(installPath, files...); err != nil {
		return err
	}
	for _, f := range files {
		fmt.Printf("Staged: %s\n", f)
	}
	return nil
}
