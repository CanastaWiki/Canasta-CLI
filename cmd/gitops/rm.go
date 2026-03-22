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
for the next gitops push. File paths can be relative to the current
directory or to the installation root.`,
		Args: cobra.MinimumNArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			return runRm(instance.Path, args)
		},
	}
	return cmd
}

func runRm(installPath string, files []string) error {
	resolved := make([]string, 0, len(files))
	for _, f := range files {
		rel, err := resolveToInstallPath(installPath, f, false)
		if err != nil {
			return err
		}
		resolved = append(resolved, rel)
	}

	if err := gitops.Remove(installPath, resolved...); err != nil {
		return err
	}
	for _, f := range resolved {
		fmt.Printf("Removed: %s\n", f)
	}
	return nil
}
