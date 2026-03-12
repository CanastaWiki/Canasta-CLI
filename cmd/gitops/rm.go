package gitops

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
)

func newRmCmd(instance *config.Installation) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "rm [files...]",
		Short: "Remove files from the gitops repository",
		Long: `Remove tracked files from the gitops repository. The removal is staged
for the next gitops push.`,
		Args: cobra.MinimumNArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			return runRm(instance.Path, args)
		},
	}
	return cmd
}

func runRm(installPath string, files []string) error {
	if err := gitops.Remove(installPath, files...); err != nil {
		return err
	}
	for _, f := range files {
		fmt.Printf("Removed: %s\n", f)
	}
	return nil
}
