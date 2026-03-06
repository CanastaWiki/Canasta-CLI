package gitops

import (
	"fmt"
	"strings"
)

// CheckPrereqs verifies that required external tools are installed.
// If needGH is true, it also checks for the GitHub CLI.
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
	return nil
}
