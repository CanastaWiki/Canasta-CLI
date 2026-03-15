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
	if !validHostName.MatchString(hostName) {
		return fmt.Errorf("invalid host name %q: must contain only alphanumeric characters, hyphens, and underscores", hostName)
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
		repair       bool
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

Use --repair to fix submodule registration in an existing gitops repository
without re-initializing. This re-converts extensions/skins that were
committed as regular directories instead of submodules.

To join an existing gitops repository instead, use "canasta gitops join".`,
		RunE: func(_ *cobra.Command, _ []string) error {
			if repair {
				return runRepairSubmodules(instance.Path)
			}
			if err := validateInitFlags(hostName, repoURL, keyFile); err != nil {
				return err
			}
			if err := gitops.ValidateRole(role); err != nil {
				return err
			}
			return runInitBootstrap(instance.Path, hostName, role, repoURL, keyFile, force, pullRequests)
		},
	}

	cmd.Flags().StringVarP(&hostName, "name", "n", "", "Name for this host in hosts.yaml")
	cmd.Flags().StringVar(&role, "role", gitops.RoleBoth, "Host role: source, sink, or both")
	cmd.Flags().StringVar(&repoURL, "repo", "", "Git repository URL")
	cmd.Flags().StringVar(&keyFile, "key", "", "Path to export the git-crypt key")
	cmd.Flags().BoolVar(&force, "force", false, "Force push to a non-empty remote repository")
	cmd.Flags().BoolVar(&pullRequests, "pull-requests", false, "Require pull requests instead of pushing directly to main")
	cmd.Flags().BoolVar(&repair, "repair", false, "Re-register extensions/skins as proper submodules")

	_ = cmd.MarkFlagRequired("name")
	_ = cmd.MarkFlagRequired("repo")
	_ = cmd.MarkFlagRequired("key")

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

	// Check for extensions/skins with uncommitted changes before doing any work.
	if dirty := findDirtyRepos(installPath); len(dirty) > 0 {
		return fmt.Errorf("the following extensions/skins have uncommitted changes:\n  %s\n"+
			"Commit or discard the changes, then re-run gitops init",
			strings.Join(dirty, "\n  "))
	}

	// Remove legacy .gitignore files from extensions/ and skins/ that were
	// created by the old Canasta-DockerCompose template. These ignore
	// everything in the directory, which prevents gitops from tracking
	// extensions and skins.
	removeLegacyGitignores(installPath)

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

	// 6. Extract wikis.yaml template.
	wikisContent, err := gitops.LoadWikisYAML(installPath)
	if err != nil {
		return err
	}
	if wikisContent != "" {
		wikisTemplate, wikisVars, err := gitops.ExtractWikisTemplate(wikisContent)
		if err != nil {
			return err
		}
		if err := gitops.SaveWikisTemplate(installPath, wikisTemplate); err != nil {
			return err
		}
		for k, v := range wikisVars {
			vars[k] = v
		}
	}

	// 7. Read admin passwords and add to vars.
	passwords, err := gitops.ReadAdminPasswords(installPath)
	if err != nil {
		logging.Print(fmt.Sprintf("Warning: could not read admin passwords: %v\n", err))
	}
	for wikiID, password := range passwords {
		vars["admin_password_"+wikiID] = password
	}

	// 8. Create hosts.yaml.
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

	// 9. Save vars.yaml.
	if err := gitops.SaveVars(installPath, hostName, vars); err != nil {
		return err
	}

	// 10. Save local host identity.
	if err := gitops.SaveLocalHost(installPath, hostName); err != nil {
		return err
	}

	// 11. Convert extensions and skins to submodules.
	if err := convertToSubmodules(installPath, "extensions"); err != nil {
		fmt.Printf("Warning: could not convert extensions to submodules: %v\n", err)
	}
	if err := convertToSubmodules(installPath, "skins"); err != nil {
		fmt.Printf("Warning: could not convert skins to submodules: %v\n", err)
	}

	// 12. Initial commit.
	if err := gitops.AddAll(installPath); err != nil {
		return err
	}
	_, err = gitops.Commit(installPath, "Initial gitops configuration")
	if err != nil {
		return err
	}

	// 13. Add remote and push.
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

// runRepairSubmodules fixes submodule registration in an existing gitops repo.
// It handles two cases:
//  1. Broken gitlinks: .gitmodules lists submodules but they were committed as
//     regular trees. The broken .git file is removed, the tree is removed from
//     the index, and the submodule is re-added properly.
//  2. Standalone git repos that need converting to submodules (same as init).
func runRepairSubmodules(installPath string) error {
	if _, err := os.Stat(filepath.Join(installPath, ".git")); os.IsNotExist(err) {
		return fmt.Errorf("not a git repository — run \"canasta gitops init\" first")
	}

	fmt.Println("Repairing submodule registration...")

	// First, fix broken gitlinks from .gitmodules.
	repaired, err := repairBrokenSubmodules(installPath)
	if err != nil {
		return err
	}

	// Then, convert any remaining standalone repos.
	for _, dirName := range []string{"extensions", "skins"} {
		if err := convertToSubmodules(installPath, dirName); err != nil {
			fmt.Printf("Warning: could not convert %s to submodules: %v\n", dirName, err)
		}
	}

	// Stage and commit if there are changes.
	if err := gitops.AddAll(installPath); err != nil {
		return err
	}
	hasChanges, _, err := gitops.HasUncommittedChanges(installPath)
	if err != nil {
		return err
	}
	if !hasChanges && repaired == 0 {
		fmt.Println("No submodule issues found.")
		return nil
	}
	if hasChanges {
		_, err = gitops.Commit(installPath, "Repair submodule registration")
		if err != nil {
			return err
		}
	}
	fmt.Printf("Repaired %d submodule(s). Run \"canasta gitops push\" to push changes.\n", repaired)
	return nil
}

// repairBrokenSubmodules reads .gitmodules and re-registers any submodules
// that were committed as regular trees instead of gitlinks (160000 commit).
func repairBrokenSubmodules(installPath string) (int, error) {
	gitmodulesPath := filepath.Join(installPath, ".gitmodules")
	if _, err := os.Stat(gitmodulesPath); os.IsNotExist(err) {
		return 0, nil
	}

	// Parse submodule entries from .gitmodules.
	output, err := execute.Run(installPath, "git", "config", "-f", ".gitmodules", "--get-regexp", `\.url$`)
	if err != nil {
		return 0, nil // no submodules defined
	}

	repaired := 0
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		if line == "" {
			continue
		}
		// Line format: submodule.extensions/Foo.url https://...
		parts := strings.SplitN(line, " ", 2)
		if len(parts) != 2 {
			continue
		}
		key := parts[0]
		url := parts[1]
		// Extract path from key: submodule.<path>.url
		path := strings.TrimPrefix(key, "submodule.")
		path = strings.TrimSuffix(path, ".url")

		// Check if it's committed as a tree (040000) instead of a submodule (160000).
		lsOutput, err := execute.Run(installPath, "git", "ls-tree", "HEAD", path)
		if err != nil || lsOutput == "" {
			continue
		}
		if !strings.HasPrefix(lsOutput, "040000") {
			continue // already a proper submodule or not tracked
		}

		fmt.Printf("Repairing %s...\n", path)

		// Remove broken .git file if present.
		gitFile := filepath.Join(installPath, path, ".git")
		if info, statErr := os.Stat(gitFile); statErr == nil && !info.IsDir() {
			os.Remove(gitFile)
		}

		// Deinit the submodule — may fail if not initialized.
		//nolint:errcheck
		execute.Run(installPath, "git", "submodule", "deinit", "-f", path)

		// Remove from index — may fail if not tracked.
		//nolint:errcheck
		execute.Run(installPath, "git", "rm", "--cached", "-r", "-q", path)

		// Clean up .git/modules/ entry and working directory.
		os.RemoveAll(filepath.Join(installPath, ".git", "modules", path))
		if err := os.RemoveAll(filepath.Join(installPath, path)); err != nil {
			return repaired, fmt.Errorf("removing %s: %w", path, err)
		}

		// Re-add as proper submodule.
		if err := gitops.SubmoduleAdd(installPath, url, path); err != nil {
			return repaired, fmt.Errorf("re-adding submodule %s: %w", path, err)
		}

		repaired++
		fmt.Printf("Repaired %s\n", path)
	}
	return repaired, nil
}

// findDirtyRepos scans extensions/ and skins/ for git repositories with
// uncommitted changes. Returns a list of relative paths (e.g.,
// "extensions/Foo") that are dirty.
func findDirtyRepos(installPath string) []string {
	var dirty []string
	for _, dirName := range []string{"extensions", "skins"} {
		dir := filepath.Join(installPath, dirName)
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, entry := range entries {
			if !entry.IsDir() {
				continue
			}
			subDir := filepath.Join(dir, entry.Name())
			info, err := os.Stat(filepath.Join(subDir, ".git"))
			if err != nil || !info.IsDir() {
				continue
			}
			if isDirty, err := isDirtyRepo(subDir); err == nil && isDirty {
				dirty = append(dirty, filepath.Join(dirName, entry.Name()))
			}
		}
	}
	return dirty
}

// convertToSubmodules scans a directory (e.g., "extensions") for
// subdirectories that are git repositories and converts them to submodules.
// The commit that was checked out is preserved after conversion.
// Callers must check for dirty repos before calling this function.
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
		info, err := os.Stat(gitDir)
		if os.IsNotExist(err) {
			continue
		}
		// A .git file (not directory) is a gitlink from a parent
		// submodule — skip it, only convert standalone repos.
		if err == nil && !info.IsDir() {
			continue
		}
		relativePath := filepath.Join(dirName, entry.Name())

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

// removeLegacyGitignores removes .gitignore files left over from the old
// Canasta-DockerCompose installation template. These files ignore everything
// except .gitignore itself, which prevents gitops from tracking the contents
// of these directories.
func removeLegacyGitignores(installPath string) {
	dirs := []string{
		"extensions",
		"skins",
		"config",
		filepath.Join("config", "settings"),
	}
	for _, dirName := range dirs {
		gi := filepath.Join(installPath, dirName, ".gitignore")
		if _, err := os.Stat(gi); err == nil {
			if err := os.Remove(gi); err == nil {
				fmt.Printf("Removed legacy %s/.gitignore\n", dirName)
			}
		}
	}
}

// requiredGitignoreEntries lists patterns that must be present in the
// .gitignore of every gitops repo. When new entries are added to the default
// template, add them here so existing repos get backfilled on next push.
var requiredGitignoreEntries = []struct {
	pattern string
	comment string
}{
	{"config/backup/", "# Database dumps created by canasta backup"},
	{"config/wikis.yaml", "# Rendered from wikis.yaml.template — host-specific URLs"},
}

// ensureGitignoreEntries appends any missing required entries to the repo's
// .gitignore. This backfills repos that were initialized before the entries
// were added to the default template.
func ensureGitignoreEntries(installPath string) error {
	giPath := filepath.Join(installPath, ".gitignore")
	data, err := os.ReadFile(giPath)
	if err != nil {
		return nil // no .gitignore means not a gitops repo
	}
	content := string(data)
	lines := strings.Split(content, "\n")

	var toAdd []struct{ pattern, comment string }
	for _, req := range requiredGitignoreEntries {
		found := false
		for _, line := range lines {
			if strings.TrimSpace(line) == req.pattern {
				found = true
				break
			}
		}
		if !found {
			toAdd = append(toAdd, req)
		}
	}

	if len(toAdd) == 0 {
		return nil
	}

	// Ensure trailing newline before appending.
	if !strings.HasSuffix(content, "\n") {
		content += "\n"
	}

	for _, entry := range toAdd {
		content += "\n" + entry.comment + "\n" + entry.pattern + "\n"
		fmt.Printf("Added %s to .gitignore\n", entry.pattern)
	}

	if err := os.WriteFile(giPath, []byte(content), 0644); err != nil {
		return err
	}

	// Remove any already-tracked files that are now gitignored.
	for _, entry := range toAdd {
		dir := filepath.Join(installPath, strings.TrimSuffix(entry.pattern, "/"))
		if _, statErr := os.Stat(dir); statErr == nil {
			// The path may not be tracked, so ignore errors.
			//nolint:errcheck
			execute.Run(installPath, "git", "rm", "-r", "--cached", "--quiet", dir)
		}
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
