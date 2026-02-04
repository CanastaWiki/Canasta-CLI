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
		return fmt.Errorf(output)
	}
	return nil
}

func Cloneb(repo, path string, branch string) error {
	err, output := execute.Run("", "git", "clone", "-b", branch, repo, path)
	if err != nil {
		return fmt.Errorf(output)
	}
	return nil
}

// FetchAndCheckout fetches from origin and selectively checks out files from
// origin/main, skipping user-modifiable files listed in skipPaths. This avoids
// merge conflicts when local commits diverge from upstream and preserves any
// files the user has customized or deleted.
func FetchAndCheckout(path string, dryRun bool) error {
	// Fetch latest from origin
	err, output := execute.Run(path, "git", "fetch", "origin")
	if err != nil {
		return fmt.Errorf(output)
	}

	// Get files that are added or modified in origin/main (safe to checkout)
	err, output = execute.Run(path, "git", "diff", "--diff-filter=d", "--name-only", "HEAD", "origin/main")
	if err != nil {
		return fmt.Errorf(output)
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
		return fmt.Errorf(output)
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
		return fmt.Errorf(output)
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
		fmt.Println("Repo is up to date with origin/main.")
		return nil
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
		// Collect denylist files that exist upstream, plus locally modified ones, deduplicated
		seen := make(map[string]bool)
		var allSkipped []string
		for _, file := range skippedExistUpstream {
			if !seen[file] {
				allSkipped = append(allSkipped, file)
				seen[file] = true
			}
		}
		for _, file := range locallyModified {
			if !seen[file] {
				allSkipped = append(allSkipped, file)
				seen[file] = true
			}
		}
		// Split by whether the file actually exists on disk
		var preservedFiles []string
		var absentFiles []string
		for _, file := range allSkipped {
			if _, err := os.Stat(filepath.Join(path, file)); err == nil {
				preservedFiles = append(preservedFiles, file)
			} else {
				absentFiles = append(absentFiles, file)
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
		return nil
	}

	// Checkout files that exist in origin/main
	if len(filesToUpdate) > 0 {
		args := append([]string{"checkout", "origin/main", "--"}, filesToUpdate...)
		err, output = execute.Run(path, "git", args...)
		if err != nil {
			return fmt.Errorf(output)
		}
	}

	// Remove files that were deleted in origin/main
	if len(filesToRemove) > 0 {
		args := append([]string{"rm", "--"}, filesToRemove...)
		err, output = execute.Run(path, "git", args...)
		if err != nil {
			return fmt.Errorf(output)
		}
	}

	return nil
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
