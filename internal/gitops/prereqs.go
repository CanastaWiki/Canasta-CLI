package gitops

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

// CheckPrereqs verifies that required external tools and configuration
// are present. If needGH is true, it also checks for the GitHub CLI.
func CheckPrereqs(needGH bool) error {
	var missing []string

	if !IsGitCryptInstalled() {
		missing = append(missing, "git-crypt (install: brew install git-crypt / sudo apt install git-crypt / sudo dnf install git-crypt)")
	}

	if needGH && !IsGHInstalled() {
		missing = append(missing, "gh (install: brew install gh / sudo apt install gh — see https://cli.github.com/)")
	}

	if len(missing) > 0 {
		return fmt.Errorf("missing required tools:\n  - %s", strings.Join(missing, "\n  - "))
	}

	if err := checkGitConfig(); err != nil {
		return err
	}

	return nil
}

// checkGitConfig verifies that git user.name and user.email are configured.
// These are required for git commit, which gitops init and push use.
func checkGitConfig() error {
	var missing []string

	name, err := execute.Run("", "git", "config", "user.name")
	if err != nil || strings.TrimSpace(name) == "" {
		missing = append(missing, "user.name")
	}

	email, err := execute.Run("", "git", "config", "user.email")
	if err != nil || strings.TrimSpace(email) == "" {
		missing = append(missing, "user.email")
	}

	if len(missing) > 0 {
		return fmt.Errorf("git %s must be configured before using gitops.\n"+
			"Run:\n"+
			"  git config --global user.name \"Your Name\"\n"+
			"  git config --global user.email \"you@example.com\"",
			strings.Join(missing, " and "))
	}
	return nil
}
