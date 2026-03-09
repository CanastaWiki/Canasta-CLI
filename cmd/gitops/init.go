package gitops

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops/defaults"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
)

var validHostName = regexp.MustCompile(`^[a-zA-Z0-9]([a-zA-Z0-9_-]*[a-zA-Z0-9])?$`)

func validateInitFlags(hostName, repoURL, keyFile string) error {
	if hostName == "" {
		return fmt.Errorf("--name is required")
	}
	if !validHostName.MatchString(hostName) {
		return fmt.Errorf("invalid host name %q: must contain only alphanumeric characters, hyphens, and underscores", hostName)
	}
	if repoURL == "" {
		return fmt.Errorf("--repo is required")
	}
	if keyFile == "" {
		return fmt.Errorf("--key is required")
	}
	return nil
}

func newInitCmd(instance *config.Installation) *cobra.Command {
	var (
		hostName     string
		role         string
		repoURL      string
		keyFile      string
		force        bool
		pullRequests bool
	)

	cmd := &cobra.Command{
		Use:   "init",
		Short: "Bootstrap a new gitops repository from an existing installation",
		Long: `Bootstrap git-based configuration management for a Canasta installation.

Sets up git, git-crypt, env.template, hosts.yaml, converts extensions/skins
to submodules, and pushes to the remote. The remote repository must be empty
(no commits). Use --force to overwrite a non-empty remote.

The git-crypt key is exported to the path specified by --key. Store this key
securely — it is needed to unlock the repo on other servers.

Use --pull-requests to require changes to go through pull requests instead of
pushing directly to main. This enables review workflows for multi-server
deployments.

To join an existing gitops repository instead, use "canasta gitops join".`,
		RunE: func(_ *cobra.Command, _ []string) error {
			if err := validateInitFlags(hostName, repoURL, keyFile); err != nil {
				return err
			}
			if err := gitops.ValidateRole(role); err != nil {
				return err
			}
			return runInitBootstrap(instance.Path, hostName, role, repoURL, keyFile, force, pullRequests)
		},
	}

	cmd.Flags().StringVarP(&hostName, "name", "n", "", "Name for this host in hosts.yaml (required)")
	cmd.Flags().StringVar(&role, "role", gitops.RoleBoth, "Host role: source, sink, or both")
	cmd.Flags().StringVar(&repoURL, "repo", "", "Git repository URL (required)")
	cmd.Flags().StringVar(&keyFile, "key", "", "Path to export the git-crypt key (required)")
	cmd.Flags().BoolVar(&force, "force", false, "Force push to a non-empty remote repository")
	cmd.Flags().BoolVar(&pullRequests, "pull-requests", false, "Require pull requests instead of pushing directly to main")
	return cmd
}

func runInitBootstrap(installPath, hostName, role, repoURL, keyFile string, force, pullRequests bool) error {
	if err := gitops.CheckPrereqs(false); err != nil {
		return err
	}

	// Check for existing git repo.
	if _, err := os.Stat(filepath.Join(installPath, ".git")); err == nil {
		return fmt.Errorf("directory is already a git repository — gitops may already be initialized")
	}

	// Check that the key file won't overwrite an existing file.
	absKeyFile, err := filepath.Abs(keyFile)
	if err != nil {
		return fmt.Errorf("resolving key file path: %w", err)
	}
	if _, err := os.Stat(absKeyFile); err == nil {
		return fmt.Errorf("key file already exists: %s\nRemove it first if you want to re-initialize", absKeyFile)
	}

	// Check the remote before doing any work.
	empty, err := gitops.IsRemoteEmpty("", repoURL)
	if err != nil {
		return fmt.Errorf("cannot access remote repository %s: %w", repoURL, err)
	}
	if !empty && !force {
		return fmt.Errorf("remote repository is not empty — bootstrap requires an empty repo\n" +
			"Use --force to overwrite the remote, or use \"canasta gitops join\" to join an existing repo")
	}

	fmt.Println("Initializing gitops repository...")

	// 1. Initialize git repo.
	if err := gitops.InitRepo(installPath); err != nil {
		return err
	}

	// 2. Write .gitignore and .gitattributes.
	if err := os.WriteFile(filepath.Join(installPath, ".gitignore"), []byte(defaults.Gitignore), permissions.FilePermission); err != nil {
		return fmt.Errorf("writing .gitignore: %w", err)
	}
	if err := os.WriteFile(filepath.Join(installPath, ".gitattributes"), []byte(defaults.Gitattributes), permissions.FilePermission); err != nil {
		return fmt.Errorf("writing .gitattributes: %w", err)
	}

	// 3. Initialize git-crypt and export the key.
	if err := gitops.GitCryptInit(installPath); err != nil {
		return err
	}
	if err := gitops.GitCryptExportKey(installPath, absKeyFile); err != nil {
		return err
	}
	fmt.Printf("git-crypt key exported to: %s\n", absKeyFile)
	fmt.Println("Store this key securely — it is needed to unlock the repo on other servers.")

	// 4. Load custom keys if present.
	customKeys, err := gitops.LoadCustomKeys(installPath)
	if err != nil {
		return err
	}

	// 5. Read current .env and create env.template + vars.
	envPath := filepath.Join(installPath, ".env")
	envContent, err := os.ReadFile(envPath)
	if err != nil {
		return fmt.Errorf("reading .env: %w", err)
	}
	placeholderKeys := gitops.AllPlaceholderKeys(customKeys)
	template, vars := gitops.ExtractTemplate(string(envContent), placeholderKeys)

	// Verify all custom keys were found in .env.
	if missing := gitops.FindMissingCustomKeys(customKeys, vars); len(missing) > 0 {
		return fmt.Errorf("custom keys not found in .env: %s\nSet them with: canasta config set %s",
			strings.Join(missing, ", "),
			strings.Join(missing, "=... ")+"=...")
	}

	if err := gitops.SaveEnvTemplate(installPath, template); err != nil {
		return err
	}

	// 6. Read admin passwords and add to vars.
	passwords, err := gitops.ReadAdminPasswords(installPath)
	if err != nil {
		logging.Print(fmt.Sprintf("Warning: could not read admin passwords: %v\n", err))
	}
	for wikiID, password := range passwords {
		vars["admin_password_"+wikiID] = password
	}

	// 7. Create hosts.yaml.
	// Use the Canasta ID from the installation registry if available.
	canastaID := filepath.Base(installPath)
	details, detailsErr := config.GetDetails(canastaID)
	if detailsErr == nil && details.ID != "" {
		canastaID = details.ID
	}

	cfg := &gitops.HostsConfig{
		CanastaID:    canastaID,
		PullRequests: pullRequests,
		Hosts: map[string]gitops.HostEntry{
			hostName: {
				Role: role,
			},
		},
	}
	if err := gitops.SaveHostsConfig(installPath, cfg); err != nil {
		return err
	}

	// 8. Save vars.yaml.
	if err := gitops.SaveVars(installPath, hostName, vars); err != nil {
		return err
	}

	// 9. Save local host identity.
	if err := gitops.SaveLocalHost(installPath, hostName); err != nil {
		return err
	}

	// 10. Convert extensions and skins to submodules.
	if err := convertToSubmodules(installPath, "extensions"); err != nil {
		logging.Print(fmt.Sprintf("Warning: could not convert extensions to submodules: %v\n", err))
	}
	if err := convertToSubmodules(installPath, "skins"); err != nil {
		logging.Print(fmt.Sprintf("Warning: could not convert skins to submodules: %v\n", err))
	}

	// 11. Initial commit.
	if err := gitops.AddAll(installPath); err != nil {
		return err
	}
	_, err = gitops.Commit(installPath, "Initial gitops configuration")
	if err != nil {
		return err
	}

	// 12. Add remote and push.
	if err := gitops.AddRemote(installPath, "origin", repoURL); err != nil {
		return err
	}
	if !empty {
		if err := gitops.ForcePush(installPath, "main"); err != nil {
			return err
		}
	} else {
		if err := gitops.Push(installPath, "main"); err != nil {
			return err
		}
	}

	fmt.Println("Gitops initialized and pushed to remote successfully.")
	return nil
}

// convertToSubmodules scans a directory (e.g., "extensions") for
// subdirectories that are git repositories and converts them to submodules.
// Directories with uncommitted changes are skipped. The commit that was
// checked out is preserved after conversion.
func convertToSubmodules(installPath, dirName string) error {
	dir := filepath.Join(installPath, dirName)
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		subDir := filepath.Join(dir, entry.Name())
		gitDir := filepath.Join(subDir, ".git")
		if _, err := os.Stat(gitDir); os.IsNotExist(err) {
			continue
		}
		relativePath := filepath.Join(dirName, entry.Name())

		// Skip directories with uncommitted changes.
		if dirty, err := isDirtyRepo(subDir); err != nil {
			logging.Print(fmt.Sprintf("Skipping %s: could not check status: %v\n", relativePath, err))
			continue
		} else if dirty {
			logging.Print(fmt.Sprintf("Skipping %s: has uncommitted changes\n", relativePath))
			continue
		}

		remoteURL := getGitRemoteURL(subDir)
		if remoteURL == "" {
			logging.Print(fmt.Sprintf("Skipping %s: no remote URL found\n", relativePath))
			continue
		}

		// Record the current commit so we can restore it after conversion.
		commitHash := getHeadCommit(subDir)

		if err := os.RemoveAll(subDir); err != nil {
			return fmt.Errorf("removing %s: %w", relativePath, err)
		}
		if err := gitops.SubmoduleAdd(installPath, remoteURL, relativePath); err != nil {
			return fmt.Errorf("adding submodule %s: %w", relativePath, err)
		}

		// Check out the original commit so the submodule is pinned to the
		// same version the user had, not the remote's default branch.
		if commitHash != "" {
			if _, err := execute.Run(subDir, "git", "checkout", commitHash); err != nil {
				logging.Print(fmt.Sprintf("Warning: could not restore %s to commit %s: %v\n", relativePath, commitHash[:8], err))
			}
		}

		logging.Print(fmt.Sprintf("Converted %s to submodule\n", relativePath))
	}
	return nil
}

func getGitRemoteURL(repoPath string) string {
	output, err := execute.Run(repoPath, "git", "remote", "get-url", "origin")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(output)
}

func isDirtyRepo(repoPath string) (bool, error) {
	output, err := execute.Run(repoPath, "git", "status", "--porcelain")
	if err != nil {
		return false, err
	}
	return strings.TrimSpace(output) != "", nil
}

func getHeadCommit(repoPath string) string {
	output, err := execute.Run(repoPath, "git", "rev-parse", "HEAD")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(output)
}
