package gitops

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
)

func newDiffCmd(instance *config.Installation) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "diff",
		Short: "Show what would change on pull (dry run)",
		Long:  `Fetch the latest changes from the remote and show what files would be updated without actually applying them.`,
		Args:  cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return runDiff(instance.Path)
		},
	}
	return cmd
}

func runDiff(installPath string) error {
	// Fetch without merging.
	if err := gitops.Fetch(installPath); err != nil {
		return err
	}

	// Compare HEAD to the upstream tracking branch.
	files, err := gitops.DiffNameOnly(installPath, "@{upstream}")
	if err != nil {
		return err
	}

	if len(files) == 0 {
		fmt.Println("No changes to pull — already up to date.")
		return nil
	}

	fmt.Printf("%d file(s) would change on pull:\n", len(files))
	for _, f := range files {
		fmt.Printf("  %s\n", f)
	}

	needsRestart, needsMaintenance, submodulesUpdated := gitops.AnalyzeChanges(files)

	if len(submodulesUpdated) > 0 {
		fmt.Println("\nSubmodules that would be updated:")
		for _, s := range submodulesUpdated {
			fmt.Printf("  %s\n", s)
		}
	}

	if needsRestart {
		fmt.Println("\nA restart would be needed after pulling.")
	}
	if needsMaintenance {
		fmt.Println("A maintenance update may be needed after pulling.")
	}

	return nil
}
