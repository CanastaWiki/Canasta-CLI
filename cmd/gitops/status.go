package gitops

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
)

func newStatusCmd(instance *config.Instance) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "status",
		Short: "Show gitops status for the instance",
		Long:  `Show the current host, role, uncommitted changes, and ahead/behind status relative to the remote.`,
		Args:  cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return runStatus(instance.Path)
		},
	}
	return cmd
}

func runStatus(installPath string) error {
	cfg, err := gitops.LoadHostsConfig(installPath)
	if err != nil {
		return err
	}

	entry, hostName, err := gitops.FindCurrentHost(cfg, installPath)
	if err != nil {
		return err
	}

	fmt.Printf("Host:           %s\n", hostName)
	fmt.Printf("Role:           %s\n", entry.Role)
	fmt.Printf("Canasta ID:     %s\n", cfg.CanastaID)
	fmt.Printf("Pull requests:  %v\n", cfg.PullRequests)

	// Current commit.
	commit, err := gitops.CurrentCommitHash(installPath)
	if err == nil {
		fmt.Printf("Current commit: %s\n", gitops.ShortHash(commit))
	}

	// Last applied commit.
	applied, err := gitops.LoadAppliedCommit(installPath)
	if err == nil && applied != "" {
		fmt.Printf("Last applied:   %s\n", gitops.ShortHash(applied))
	}

	// Working tree status.
	stagedFiles, unstagedFiles, err := gitops.WorkingTreeStatus(installPath)
	if err == nil {
		if len(stagedFiles) > 0 {
			fmt.Printf("\nStaged for push (%d files):\n", len(stagedFiles))
			for _, f := range stagedFiles {
				fmt.Printf("  %s\n", f)
			}
		}
		if len(unstagedFiles) > 0 {
			fmt.Printf("\nUnstaged changes (%d files):\n", len(unstagedFiles))
			for _, f := range unstagedFiles {
				fmt.Printf("  %s\n", f)
			}
		}
		if len(stagedFiles) == 0 && len(unstagedFiles) == 0 {
			fmt.Println("\nNo changes.")
		}
	}

	// Ahead/behind remote.
	if err := gitops.Fetch(installPath); err == nil {
		ahead, behind, err := gitops.AheadBehind(installPath)
		if err == nil {
			if ahead == 0 && behind == 0 {
				fmt.Println("Up to date with remote.")
			} else {
				if ahead > 0 {
					fmt.Printf("Ahead of remote by %d commit(s).\n", ahead)
				}
				if behind > 0 {
					fmt.Printf("Behind remote by %d commit(s).\n", behind)
				}
			}
		}
	}

	return nil
}
