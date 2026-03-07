package gitops

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops/defaults"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
)

func newInitCmd(instance *config.Installation) *cobra.Command {
	var (
		hostName string
		role     string
		repoURL  string
		keyFile  string
		force    bool
	)

	cmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize gitops for an installation",
		Long: `Initialize git-based configuration management for a Canasta installation.

Without --key: bootstrap a new gitops repository from the existing installation.
Sets up git, git-crypt, env.template, hosts.yaml, converts extensions/skins
to submodules, and pushes to the remote. The remote repository must be empty
(no commits). Use --force to overwrite a non-empty remote.

With --key: join an existing gitops repository. Clones the repo, unlocks
git-crypt, extracts host-specific values, and overlays shared configuration.`,
		RunE: func(_ *cobra.Command, _ []string) error {
			if hostName == "" {
				return fmt.Errorf("--name is required")
			}
			if repoURL == "" {
				return fmt.Errorf("--repo is required")
			}
			if err := gitops.ValidateRole(role); err != nil {
				return err
			}
			if keyFile != "" {
				return runInitJoin(instance.Path, hostName, role, repoURL, keyFile)
			}
			return runInitBootstrap(instance.Path, hostName, role, repoURL, force)
		},
	}

	cmd.Flags().StringVarP(&hostName, "name", "n", "", "Name for this host in hosts.yaml (required)")
	cmd.Flags().StringVar(&role, "role", gitops.RoleBoth, "Host role: source, sink, or both")
	cmd.Flags().StringVar(&repoURL, "repo", "", "Git repository URL (required)")
	cmd.Flags().StringVar(&keyFile, "key", "", "Path to git-crypt key file (required to join an existing repo)")
	cmd.Flags().BoolVar(&force, "force", false, "Force push to a non-empty remote repository (bootstrap only)")
	return cmd
}

func runInitBootstrap(installPath, hostName, role, repoURL string, force bool) error {
	if err := gitops.CheckPrereqs(false); err != nil {
		return err
	}

	// Check for existing git repo.
	if _, err := os.Stat(filepath.Join(installPath, ".git")); err == nil {
		return fmt.Errorf("directory is already a git repository — gitops may already be initialized")
	}

	// 0. Check the remote before doing any work.
	empty, err := gitops.IsRemoteEmpty("", repoURL)
	if err != nil {
		return fmt.Errorf("cannot access remote repository %s: %w", repoURL, err)
	}
	if !empty && !force {
		return fmt.Errorf("remote repository is not empty — bootstrap requires an empty repo\n" +
			"Use --force to overwrite the remote, or --key to join an existing gitops repo")
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
	keyFile := filepath.Join(installPath, "..", fmt.Sprintf("gitops-key-%s", hostName))
	if err := gitops.GitCryptExportKey(installPath, keyFile); err != nil {
		return err
	}
	fmt.Printf("git-crypt key exported to: %s\n", keyFile)
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
		CanastaID: canastaID,
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

func runInitJoin(installPath, hostName, role, repoURL, keyFile string) error {
	if keyFile == "" {
		return fmt.Errorf("--key is required when joining an existing repo (--repo)")
	}
	if err := gitops.CheckPrereqs(false); err != nil {
		return err
	}

	// Check for existing git repo.
	if _, err := os.Stat(filepath.Join(installPath, ".git")); err == nil {
		return fmt.Errorf("directory is already a git repository — cannot join gitops repo")
	}

	fmt.Println("Joining existing gitops repository...")

	// 1. Clone the repo to a temp dir, then merge into the installation.
	tmpDir, err := os.MkdirTemp("", "canasta-gitops-*")
	if err != nil {
		return fmt.Errorf("creating temp directory: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	if err := gitops.Clone(repoURL, tmpDir); err != nil {
		return err
	}

	absKeyFile, err := filepath.Abs(keyFile)
	if err != nil {
		return fmt.Errorf("resolving key file path: %w", err)
	}
	if err := gitops.GitCryptUnlock(tmpDir, absKeyFile); err != nil {
		return err
	}

	// 2. Load the hosts config from the cloned repo and validate
	// the host name before modifying the installation directory.
	cfg, err := gitops.LoadHostsConfig(tmpDir)
	if err != nil {
		return err
	}
	if _, exists := cfg.Hosts[hostName]; exists {
		return fmt.Errorf("host %q already exists in %s — choose a different name with --name", hostName, "hosts.yaml")
	}

	// Move .git directory into the installation.
	if err := moveGitDir(tmpDir, installPath); err != nil {
		return err
	}

	// Checkout tracked files from the repo into the installation directory.
	// git-crypt is already unlocked (carried over from the temp clone), so
	// the checked-out files will be decrypted.
	if err := gitops.CheckoutHead(installPath); err != nil {
		return fmt.Errorf("checking out repo files: %w", err)
	}

	// 3. Add this host.
	cfg.Hosts[hostName] = gitops.HostEntry{
		Role: role,
	}
	if err := gitops.SaveHostsConfig(installPath, cfg); err != nil {
		return err
	}

	// 4. Extract current values into vars.yaml.
	customKeys, err := gitops.LoadCustomKeys(installPath)
	if err != nil {
		return err
	}

	envPath := filepath.Join(installPath, ".env")
	envContent, err := os.ReadFile(envPath)
	if err != nil {
		return fmt.Errorf("reading .env: %w", err)
	}
	placeholderKeys := gitops.AllPlaceholderKeys(customKeys)
	_, vars := gitops.ExtractTemplate(string(envContent), placeholderKeys)

	// Verify all custom keys were found in .env.
	if missing := gitops.FindMissingCustomKeys(customKeys, vars); len(missing) > 0 {
		return fmt.Errorf("custom keys not found in .env: %s\nSet them with: canasta config set %s",
			strings.Join(missing, ", "),
			strings.Join(missing, "=... ")+"=...")
	}

	passwords, err := gitops.ReadAdminPasswords(installPath)
	if err != nil {
		logging.Print(fmt.Sprintf("Warning: could not read admin passwords: %v\n", err))
	}
	for wikiID, password := range passwords {
		vars["admin_password_"+wikiID] = password
	}
	if err := gitops.SaveVars(installPath, hostName, vars); err != nil {
		return err
	}

	// 4b. Save local host identity.
	if err := gitops.SaveLocalHost(installPath, hostName); err != nil {
		return err
	}

	// 4d. Render .env from the repo's env.template with this host's vars.
	tmpl, err := gitops.LoadEnvTemplate(installPath)
	if err != nil {
		return err
	}
	newEnv, err := gitops.RenderTemplate(tmpl, vars)
	if err != nil {
		return fmt.Errorf("rendering env.template: %w", err)
	}
	if err := os.WriteFile(envPath, []byte(newEnv), permissions.SecretFilePermission); err != nil {
		return fmt.Errorf("writing .env: %w", err)
	}

	// 4e. Write admin password files from vars.
	if err := gitops.WriteAdminPasswords(installPath, vars); err != nil {
		return err
	}

	// 5. Update submodules.
	if err := gitops.SubmoduleUpdate(installPath); err != nil {
		logging.Print(fmt.Sprintf("Warning: submodule update: %v\n", err))
	}

	// 6. Commit and push.
	if err := gitops.AddAll(installPath); err != nil {
		return err
	}

	hasChanges, _, err := gitops.HasUncommittedChanges(installPath)
	if err != nil {
		return err
	}
	if !hasChanges {
		fmt.Println("No changes to commit.")
		return nil
	}

	message := fmt.Sprintf("Add host %s", hostName)

	if cfg.PullRequests {
		if !gitops.IsGHInstalled() {
			return fmt.Errorf("pull_requests is enabled but gh CLI is not installed")
		}
		branchName := fmt.Sprintf("add-host-%s", hostName)
		if err := gitops.CreateBranch(installPath, branchName); err != nil {
			return err
		}
		if _, err := gitops.Commit(installPath, message); err != nil {
			return err
		}
		if err := gitops.Push(installPath, branchName); err != nil {
			return err
		}
		prURL, err := gitops.CreatePR(installPath, message, fmt.Sprintf("Add host %s to gitops management.", hostName))
		if err != nil {
			return err
		}
		fmt.Printf("Pull request created: %s\n", prURL)
		fmt.Println("After the PR is merged, run: canasta gitops pull")
		if err := gitops.CheckoutMain(installPath); err != nil {
			return err
		}
	} else {
		if _, err := gitops.Commit(installPath, message); err != nil {
			return err
		}
		if err := gitops.Push(installPath, "main"); err != nil {
			return err
		}
	}

	fmt.Println("Successfully joined the gitops repository.")
	return nil
}

// convertToSubmodules scans a directory (e.g., "extensions") for
// subdirectories that are git repositories and converts them to submodules.
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
		remoteURL := getGitRemoteURL(subDir)
		if remoteURL == "" {
			logging.Print(fmt.Sprintf("Skipping %s/%s: no remote URL found\n", dirName, entry.Name()))
			continue
		}
		relativePath := filepath.Join(dirName, entry.Name())
		if err := os.RemoveAll(subDir); err != nil {
			return fmt.Errorf("removing %s: %w", relativePath, err)
		}
		if err := gitops.SubmoduleAdd(installPath, remoteURL, relativePath); err != nil {
			return fmt.Errorf("adding submodule %s: %w", relativePath, err)
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

func moveGitDir(srcDir, dstDir string) error {
	srcGit := filepath.Join(srcDir, ".git")
	dstGit := filepath.Join(dstDir, ".git")
	if err := os.Rename(srcGit, dstGit); err != nil {
		// os.Rename fails across filesystems; fall back to copy + remove.
		if err := copyDir(srcGit, dstGit); err != nil {
			return fmt.Errorf("copying .git directory: %w", err)
		}
		if err := os.RemoveAll(srcGit); err != nil {
			return fmt.Errorf("removing source .git directory: %w", err)
		}
	}
	return nil
}

// copyDir recursively copies a directory tree.
func copyDir(src, dst string) error {
	info, err := os.Stat(src)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dst, info.Mode()); err != nil {
		return err
	}
	entries, err := os.ReadDir(src)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		srcPath := filepath.Join(src, entry.Name())
		dstPath := filepath.Join(dst, entry.Name())
		if entry.IsDir() {
			if err := copyDir(srcPath, dstPath); err != nil {
				return err
			}
		} else {
			data, err := os.ReadFile(srcPath)
			if err != nil {
				return err
			}
			eInfo, err := entry.Info()
			if err != nil {
				return err
			}
			if err := os.WriteFile(dstPath, data, eInfo.Mode()); err != nil {
				return err
			}
		}
	}
	return nil
}
