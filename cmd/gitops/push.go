package gitops

import (
	"bufio"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
)

func newPushCmd(instance *config.Installation) *cobra.Command {
	var message string

	cmd := &cobra.Command{
		Use:   "push",
		Short: "Push configuration changes to the git repository",
		Long: `Commit staged changes and push to the remote repository. Files must be
staged first using "canasta gitops add". When pull_requests is enabled in
hosts.yaml, creates a branch and opens a pull request instead of pushing
directly to main.`,
		RunE: func(_ *cobra.Command, _ []string) error {
			result, err := runPush(instance.Path, message)
			if err != nil {
				return err
			}
			printPushResult(result)
			return nil
		},
	}

	cmd.Flags().StringVarP(&message, "message", "m", "", "Commit message")
	return cmd
}

func runPush(installPath, message string) (*gitops.PushResult, error) {
	cfg, err := gitops.LoadHostsConfig(installPath)
	if err != nil {
		return nil, err
	}

	entry, hostName, err := gitops.FindCurrentHost(cfg, installPath)
	if err != nil {
		return nil, err
	}

	if !gitops.CanPush(entry.Role) {
		return nil, fmt.Errorf("host %q has role %q and cannot push", hostName, entry.Role)
	}

	if err := gitops.CheckPrereqs(cfg.PullRequests); err != nil {
		return nil, err
	}

	// Ensure the .gitignore has all required entries (backfill for repos
	// initialized before new entries were added to the default template).
	if err := ensureGitignoreEntries(installPath); err != nil {
		return nil, err
	}

	hasStaged, files, err := gitops.HasStagedChanges(installPath)
	if err != nil {
		return nil, err
	}
	if !hasStaged {
		return &gitops.PushResult{NoChanges: true}, nil
	}

	fmt.Printf("Files to be committed (%d):\n", len(files))
	for _, f := range files {
		fmt.Printf("  %s\n", f)
	}

	if message == "" {
		fmt.Print("Commit message: ")
		reader := bufio.NewReader(os.Stdin)
		message, err = reader.ReadString('\n')
		if err != nil {
			return nil, fmt.Errorf("reading commit message: %w", err)
		}
		message = strings.TrimSpace(message)
		if message == "" {
			return nil, fmt.Errorf("commit message cannot be empty")
		}
	}

	result := &gitops.PushResult{}

	if cfg.PullRequests {
		branchName := fmt.Sprintf("gitops-%s-%s", hostName, time.Now().Format("20060102-150405"))
		if err := gitops.CreateBranch(installPath, branchName); err != nil {
			return nil, err
		}
		hash, err := gitops.Commit(installPath, message)
		if err != nil {
			return nil, err
		}
		if err := gitops.Push(installPath, branchName); err != nil {
			return nil, err
		}
		prURL, err := gitops.CreatePR(installPath, message, "")
		if err != nil {
			return nil, err
		}
		result.CommitHash = hash
		result.Branch = branchName
		result.PRURL = prURL

		if err := gitops.CheckoutMain(installPath); err != nil {
			return nil, err
		}
	} else {
		hash, err := gitops.Commit(installPath, message)
		if err != nil {
			return nil, err
		}
		if err := gitops.Push(installPath, "main"); err != nil {
			return nil, err
		}
		result.CommitHash = hash
		result.Branch = "main"
	}

	return result, nil
}

func printPushResult(result *gitops.PushResult) {
	if result.NoChanges {
		fmt.Println("No changes to push.")
		return
	}
	fmt.Printf("Committed: %s\n", result.CommitHash)
	if result.PRURL != "" {
		fmt.Printf("Pull request: %s\n", result.PRURL)
	} else {
		fmt.Printf("Pushed to %s\n", result.Branch)
	}
}
