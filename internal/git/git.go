package git

import (
	"fmt"
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
func FetchAndCheckout(path string) error {
	// Fetch latest from origin
	err, output := execute.Run(path, "git", "fetch", "origin")
	if err != nil {
		return fmt.Errorf(output)
	}

	// Get list of files that differ between local and upstream
	err, output = execute.Run(path, "git", "diff", "--name-only", "HEAD", "origin/main")
	if err != nil {
		return fmt.Errorf(output)
	}

	// Filter out denied paths
	var filesToUpdate []string
	for _, file := range strings.Split(strings.TrimSpace(output), "\n") {
		if file == "" {
			continue
		}
		if !isSkipped(file) {
			filesToUpdate = append(filesToUpdate, file)
		}
	}

	if len(filesToUpdate) == 0 {
		return nil
	}

	// Checkout only the safe files from origin/main
	args := append([]string{"checkout", "origin/main", "--"}, filesToUpdate...)
	err, output = execute.Run(path, "git", args...)
	if err != nil {
		return fmt.Errorf(output)
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
