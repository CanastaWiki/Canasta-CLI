package git

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

// skipPaths are files/directories that should never be overwritten during upgrade.
// Users may modify or delete these locally.
var skipPaths = []string{
	"my.cnf",
	"docker-compose.override.yml",
	"config/settings/",
	"config/Caddyfile.custom",
	"config/composer.local.json",
	"config/default.vcl",
}

func Clone(repo, path string) error {
	err, output := execute.Run("", "git", "clone", repo, path)
	if err != nil {
		return fmt.Errorf("%s", output)
	}
	return nil
}

func Cloneb(repo, path string, branch string) error {
	err, output := execute.Run("", "git", "clone", "-b", branch, repo, path)
	if err != nil {
		return fmt.Errorf("%s", output)
	}
	return nil
}

// FetchAndCheckout fetches from origin and selectively checks out files from
// origin/main, skipping user-modifiable files listed in skipPaths. This avoids
// merge conflicts when local commits diverge from upstream and preserves any
// files the user has customized or deleted.
func FetchAndCheckout(path string, dryRun bool) (bool, error) {
	// Fetch latest from origin
	err, output := execute.Run(path, "git", "fetch", "origin")
	if err != nil {
		return false, fmt.Errorf("%s", output)
	}

	// Get files that are added or modified in origin/main (safe to checkout)
	err, output = execute.Run(path, "git", "diff", "--diff-filter=d", "--name-only", "HEAD", "origin/main")
	if err != nil {
		return false, fmt.Errorf("%s", output)
	}

	var filesToUpdate []string
	var skippedExistUpstream []string // denylist files that exist in origin/main
	for _, file := range strings.Split(strings.TrimSpace(output), "\n") {
		if file == "" {
			continue
		}
		if isSkipped(file) {
			skippedExistUpstream = append(skippedExistUpstream, file)
		} else {
			filesToUpdate = append(filesToUpdate, file)
		}
	}

	// Get files that were deleted in origin/main
	err, output = execute.Run(path, "git", "diff", "--diff-filter=D", "--name-only", "HEAD", "origin/main")
	if err != nil {
		return false, fmt.Errorf("%s", output)
	}

	var filesToRemove []string
	for _, file := range strings.Split(strings.TrimSpace(output), "\n") {
		if file == "" {
			continue
		}
		if !isSkipped(file) {
			filesToRemove = append(filesToRemove, file)
		}
	}

	// Get denylist files with uncommitted local modifications (working tree vs HEAD).
	// These won't appear in the HEAD vs origin/main diff if the committed versions match.
	err, output = execute.Run(path, "git", "diff", "--name-only", "HEAD")
	if err != nil {
		return false, fmt.Errorf("%s", output)
	}

	var locallyModified []string
	for _, file := range strings.Split(strings.TrimSpace(output), "\n") {
		if file == "" {
			continue
		}
		if isSkipped(file) {
			locallyModified = append(locallyModified, file)
		}
	}

	if len(filesToUpdate) == 0 && len(filesToRemove) == 0 && len(skippedExistUpstream) == 0 && len(locallyModified) == 0 {
		fmt.Println("Configuration files are up to date.")
		return false, nil
	}

	// Categorize preserved and absent files (used for output in both modes)
	seen := make(map[string]bool)
	var preservedFiles []string
	var absentFiles []string
	// Files that exist in origin/main: split into preserved (on disk) or absent
	for _, file := range skippedExistUpstream {
		if !seen[file] {
			seen[file] = true
			if _, err := os.Stat(filepath.Join(path, file)); err == nil {
				preservedFiles = append(preservedFiles, file)
			} else {
				absentFiles = append(absentFiles, file)
			}
		}
	}
	// Locally modified denylist files: only add to preserved if on disk
	// (don't add to absent — if they don't exist upstream they'd never be restored)
	for _, file := range locallyModified {
		if !seen[file] {
			seen[file] = true
			if _, err := os.Stat(filepath.Join(path, file)); err == nil {
				preservedFiles = append(preservedFiles, file)
			}
		}
	}

	if dryRun {
		if len(filesToUpdate) > 0 {
			fmt.Println("Files that would be updated from upstream:")
			for _, file := range filesToUpdate {
				fmt.Printf("  %s\n", file)
			}
		}
		if len(filesToRemove) > 0 {
			fmt.Println("Files that would be removed (deleted upstream):")
			for _, file := range filesToRemove {
				fmt.Printf("  %s\n", file)
			}
		}
		if len(preservedFiles) > 0 {
			fmt.Println("Files with local changes that would be preserved:")
			for _, file := range preservedFiles {
				fmt.Printf("  %s\n", file)
			}
		}
		if len(absentFiles) > 0 {
			fmt.Println("Files absent locally that would not be restored from upstream:")
			for _, file := range absentFiles {
				fmt.Printf("  %s\n", file)
			}
		}
		return len(filesToUpdate) > 0 || len(filesToRemove) > 0, nil
	}

	// Checkout files that exist in origin/main
	if len(filesToUpdate) > 0 {
		args := append([]string{"checkout", "origin/main", "--"}, filesToUpdate...)
		err, output = execute.Run(path, "git", args...)
		if err != nil {
			return false, fmt.Errorf("%s", output)
		}
		fmt.Println("Files updated from upstream:")
		for _, file := range filesToUpdate {
			fmt.Printf("  %s\n", file)
		}
	}

	// Remove files that were deleted in origin/main
	if len(filesToRemove) > 0 {
		for _, file := range filesToRemove {
			filePath := filepath.Join(path, file)
			if err := os.Remove(filePath); err != nil && !os.IsNotExist(err) {
				return false, fmt.Errorf("failed to remove %s: %w", file, err)
			}
		}
		fmt.Println("Files removed (deleted upstream):")
		for _, file := range filesToRemove {
			fmt.Printf("  %s\n", file)
		}
	}

	// Print preserved and absent files
	if len(preservedFiles) > 0 {
		fmt.Println("Files with local changes preserved:")
		for _, file := range preservedFiles {
			fmt.Printf("  %s\n", file)
		}
	}
	if len(absentFiles) > 0 {
		fmt.Println("Files absent locally not restored from upstream:")
		for _, file := range absentFiles {
			fmt.Printf("  %s\n", file)
		}
	}

	// Move HEAD and index to origin/main so future diffs reflect the updated state.
	// Working tree is left as-is (denylist files keep their local changes).
	err, output = execute.Run(path, "git", "reset", "origin/main")
	if err != nil {
		return false, fmt.Errorf("%s", output)
	}

	return true, nil
}

// isSkipped returns true if the file matches any entry in skipPaths.
// Directory entries (ending in /) match any file under that path.
func isSkipped(file string) bool {
	for _, skip := range skipPaths {
		if strings.HasSuffix(skip, "/") {
			if strings.HasPrefix(file, skip) {
				return true
			}
		} else if file == skip {
			return true
		}
	}
	return false
}
