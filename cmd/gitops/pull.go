package gitops

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
)

func newPullCmd(instance *config.Installation) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "pull",
		Short: "Pull latest configuration from the git repository",
		Long: `Pull the latest configuration from the remote repository, update submodules,
render .env and admin password files from the template and host vars, and
report what changed and whether a restart is needed.`,
		RunE: func(_ *cobra.Command, _ []string) error {
			result, err := runPull(instance.Path)
			if err != nil {
				return err
			}
			printPullResult(result)
			return nil
		},
	}
	return cmd
}

func runPull(installPath string) (*gitops.PullResult, error) {
	// 1. Check for uncommitted local changes.
	hasChanges, files, err := gitops.HasUncommittedChanges(installPath)
	if err != nil {
		return nil, err
	}
	if hasChanges {
		return nil, fmt.Errorf("uncommitted local changes detected — push or discard them before pulling:\n  %s",
			strings.Join(files, "\n  "))
	}

	// Record the current commit before pulling.
	prevCommit, err := gitops.CurrentCommitHash(installPath)
	if err != nil {
		return nil, err
	}

	// Save a copy of the current .env for comparison.
	envPath := filepath.Join(installPath, ".env")
	oldEnv, _ := os.ReadFile(envPath)

	// 2. Git pull.
	if err := gitops.Pull(installPath); err != nil {
		return nil, err
	}

	// 3. Update submodules.
	if err := gitops.SubmoduleUpdate(installPath); err != nil {
		return nil, fmt.Errorf("submodule update: %w", err)
	}

	// Get the new commit.
	newCommit, err := gitops.CurrentCommitHash(installPath)
	if err != nil {
		return nil, err
	}

	if prevCommit == newCommit {
		return &gitops.PullResult{
			NoChanges:      true,
			PreviousCommit: prevCommit,
			CurrentCommit:  newCommit,
		}, nil
	}

	// 4. Find current host.
	cfg, err := gitops.LoadHostsConfig(installPath)
	if err != nil {
		return nil, err
	}

	_, hostName, err := gitops.FindCurrentHost(cfg)
	if err != nil {
		return nil, err
	}

	// 5. Load vars and render .env.
	vars, err := gitops.LoadVars(installPath, hostName)
	if err != nil {
		return nil, err
	}

	tmpl, err := gitops.LoadEnvTemplate(installPath)
	if err != nil {
		return nil, err
	}

	newEnvContent, err := gitops.RenderTemplate(tmpl, vars)
	if err != nil {
		return nil, fmt.Errorf("rendering env.template: %w", err)
	}

	if err := os.WriteFile(envPath, []byte(newEnvContent), 0644); err != nil {
		return nil, fmt.Errorf("writing .env: %w", err)
	}

	// 6. Write admin password files.
	if err := gitops.WriteAdminPasswords(installPath, vars); err != nil {
		return nil, err
	}

	// 7. Determine what changed.
	changedFiles, err := gitops.DiffNameOnly(installPath, prevCommit)
	if err != nil {
		// Non-fatal: we can still report success without the diff.
		changedFiles = nil
	}

	needsRestart, needsMaintenance, submodulesUpdated := gitops.AnalyzeChanges(changedFiles)

	// Also check if .env content actually changed.
	if string(oldEnv) != newEnvContent {
		needsRestart = true
	}

	// 8. Save the applied commit.
	if err := gitops.SaveAppliedCommit(installPath, newCommit); err != nil {
		// Non-fatal.
		_ = err
	}

	return &gitops.PullResult{
		ChangedFiles:      changedFiles,
		NeedsRestart:      needsRestart,
		NeedsMaintenance:  needsMaintenance,
		SubmodulesUpdated: submodulesUpdated,
		PreviousCommit:    prevCommit,
		CurrentCommit:     newCommit,
	}, nil
}

func printPullResult(result *gitops.PullResult) {
	if result.NoChanges {
		fmt.Println("Already up to date.")
		return
	}

	fmt.Printf("Updated: %s → %s\n", result.PreviousCommit[:8], result.CurrentCommit[:8])

	if len(result.ChangedFiles) > 0 {
		fmt.Println("Changed files:")
		for _, f := range result.ChangedFiles {
			fmt.Printf("  %s\n", f)
		}
	}

	if len(result.SubmodulesUpdated) > 0 {
		fmt.Println("Updated submodules:")
		for _, s := range result.SubmodulesUpdated {
			fmt.Printf("  %s\n", s)
		}
	}

	if result.NeedsRestart {
		fmt.Println("\nRestart needed: run 'canasta restart' to apply changes.")
	}
	if result.NeedsMaintenance {
		fmt.Println("Maintenance update may be needed: run 'canasta maintenance update' if schema changes are expected.")
	}
	if !result.NeedsRestart && !result.NeedsMaintenance {
		fmt.Println("No restart needed — changes take effect on next request.")
	}
}
