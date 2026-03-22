package gitops

import (
	"fmt"
	"sort"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
)

func newDiffCmd(instance *config.Installation) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "diff",
		Short: "Show local and remote changes since the branches diverged",
		Long: `Show uncommitted working tree changes, then fetch the latest from
the remote and show what files have changed locally, what files have
changed on the remote, and which files have changed in both
(potential merge conflicts).`,
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return runDiff(instance.Path)
		},
	}
	return cmd
}

func runDiff(installPath string) error {
	// Check for uncommitted working tree changes first.
	hasUncommitted, uncommittedFiles, err := gitops.HasUncommittedChanges(installPath)
	if err != nil {
		return err
	}

	// Fetch without merging.
	if err := gitops.Fetch(installPath); err != nil {
		return err
	}

	// Files changed locally since the fork point.
	localFiles, err := gitops.DiffThreeDot(installPath, "@{upstream}", "HEAD")
	if err != nil {
		return err
	}

	// Files changed on the remote since the fork point.
	remoteFiles, err := gitops.DiffThreeDot(installPath, "HEAD", "@{upstream}")
	if err != nil {
		return err
	}

	if !hasUncommitted && len(localFiles) == 0 && len(remoteFiles) == 0 {
		fmt.Println("No changes — local and remote are in sync.")
		return nil
	}

	if hasUncommitted {
		fmt.Printf("Uncommitted changes: %d file(s)\n", len(uncommittedFiles))
		for _, f := range uncommittedFiles {
			fmt.Printf("  %s\n", f)
		}
		fmt.Println()
	}

	localOnly, remoteOnly, conflicts := classifyChanges(localFiles, remoteFiles)

	if len(localOnly) > 0 {
		fmt.Printf("Local changes (not yet pushed): %d file(s)\n", len(localOnly))
		for _, f := range localOnly {
			fmt.Printf("  %s\n", f)
		}
	}

	if len(remoteOnly) > 0 {
		if len(localOnly) > 0 {
			fmt.Println()
		}
		fmt.Printf("Remote changes (would be applied on pull): %d file(s)\n", len(remoteOnly))
		for _, f := range remoteOnly {
			fmt.Printf("  %s\n", f)
		}
	}

	if len(conflicts) > 0 {
		fmt.Printf("\nPotential conflicts (changed in both): %d file(s)\n", len(conflicts))
		for _, f := range conflicts {
			fmt.Printf("  %s\n", f)
		}
	}

	// Analyze remote changes for restart/maintenance hints.
	allRemote := make([]string, 0, len(remoteOnly)+len(conflicts))
	allRemote = append(allRemote, remoteOnly...)
	allRemote = append(allRemote, conflicts...)
	if len(allRemote) > 0 {
		needsRestart, needsMaintenance, submodulesUpdated := gitops.AnalyzeChanges(allRemote)

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
	}

	return nil
}

// classifyChanges partitions local and remote file lists into three sorted
// slices: files changed only locally, files changed only on the remote,
// and files changed in both (potential conflicts).
func classifyChanges(localFiles, remoteFiles []string) (localOnly, remoteOnly, conflicts []string) {
	localSet := make(map[string]bool, len(localFiles))
	for _, f := range localFiles {
		localSet[f] = true
	}
	remoteSet := make(map[string]bool, len(remoteFiles))
	for _, f := range remoteFiles {
		remoteSet[f] = true
	}

	for _, f := range localFiles {
		if remoteSet[f] {
			conflicts = append(conflicts, f)
		} else {
			localOnly = append(localOnly, f)
		}
	}
	for _, f := range remoteFiles {
		if !localSet[f] {
			remoteOnly = append(remoteOnly, f)
		}
	}

	sort.Strings(localOnly)
	sort.Strings(remoteOnly)
	sort.Strings(conflicts)
	return
}
