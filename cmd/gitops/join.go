package gitops

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
)

func newJoinCmd(instance *config.Installation) *cobra.Command {
	var (
		hostName string
		role     string
		repoURL  string
		keyFile  string
	)

	cmd := &cobra.Command{
		Use:   "join",
		Short: "Join an existing gitops repository",
		Long: `Join an existing gitops repository for a Canasta installation.

Clones the repo, unlocks git-crypt with the provided key, adds this host
to the host inventory, extracts host-specific values into vars.yaml, and
pushes the new host entry back to the repo.

The installation must already exist and have a working .env file. Use
"canasta gitops init" to bootstrap a new gitops repository instead.`,
		RunE: func(_ *cobra.Command, _ []string) error {
			if err := validateInitFlags(hostName); err != nil {
				return err
			}
			if err := gitops.ValidateRole(role); err != nil {
				return err
			}
			return runInitJoin(instance.Path, hostName, role, repoURL, keyFile)
		},
	}

	cmd.Flags().StringVarP(&hostName, "name", "n", "", "Name for this host in hosts.yaml")
	cmd.Flags().StringVar(&role, "role", gitops.RoleBoth, "Host role: source, sink, or both")
	cmd.Flags().StringVar(&repoURL, "repo", "", "Git repository URL")
	cmd.Flags().StringVar(&keyFile, "key", "", "Path to the git-crypt key file")

	_ = cmd.MarkFlagRequired("name")
	_ = cmd.MarkFlagRequired("repo")
	_ = cmd.MarkFlagRequired("key")

	return cmd
}

func runInitJoin(installPath, hostName, role, repoURL, keyFile string) error {
	if err := gitops.CheckPrereqs(false); err != nil {
		return err
	}

	// Check for existing git repo.
	if _, err := os.Stat(filepath.Join(installPath, ".git")); err == nil {
		return fmt.Errorf("directory is already a git repository — gitops may already be initialized")
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

	// Extract wiki URLs from the local wikis.yaml into vars.
	wikisContent, err := gitops.LoadWikisYAML(installPath)
	if err != nil {
		return err
	}
	if wikisContent != "" {
		_, wikisVars, err := gitops.ExtractWikisTemplate(wikisContent)
		if err != nil {
			return err
		}
		for k, v := range wikisVars {
			vars[k] = v
		}
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

	// 5. Save local host identity.
	if err := gitops.SaveLocalHost(installPath, hostName); err != nil {
		return err
	}

	// 6. Render .env and wikis.yaml in memory (written to disk after push succeeds).
	tmpl, err := gitops.LoadEnvTemplate(installPath)
	if err != nil {
		return err
	}
	newEnv, err := gitops.RenderTemplate(tmpl, vars)
	if err != nil {
		return fmt.Errorf("rendering env.template: %w", err)
	}

	var newWikis string
	wikisTmpl, err := gitops.LoadWikisTemplate(installPath)
	if err != nil {
		return err
	}
	if wikisTmpl != "" {
		newWikis, err = gitops.RenderWikisTemplate(wikisTmpl, vars)
		if err != nil {
			return fmt.Errorf("rendering wikis.yaml.template: %w", err)
		}
	}

	// 7. Update submodules.
	if err := gitops.SubmoduleUpdate(installPath); err != nil {
		logging.Print(fmt.Sprintf("Warning: submodule update: %v\n", err))
	}

	// 8. Commit and push.
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

	// 9. Write .env, wikis.yaml, and admin passwords now that the push succeeded.
	if err := os.WriteFile(envPath, []byte(newEnv), permissions.SecretFilePermission); err != nil {
		return fmt.Errorf("writing .env: %w", err)
	}
	if newWikis != "" {
		wikisPath := filepath.Join(installPath, "config", "wikis.yaml")
		if err := os.WriteFile(wikisPath, []byte(newWikis), permissions.FilePermission); err != nil {
			return fmt.Errorf("writing wikis.yaml: %w", err)
		}
	}
	if err := gitops.WriteAdminPasswords(installPath, vars); err != nil {
		return err
	}

	fmt.Println("Successfully joined the gitops repository.")
	return nil
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
