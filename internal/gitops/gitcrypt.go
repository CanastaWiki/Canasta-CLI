package gitops

import (
	"fmt"
	"os/exec"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

// GitCryptInit initializes git-crypt in the repository.
func GitCryptInit(path string) error {
	_, err := execute.Run(path, "git-crypt", "init")
	if err != nil {
		return fmt.Errorf("git-crypt init: %w", err)
	}
	return nil
}

// GitCryptExportKey exports the symmetric git-crypt key to a file.
func GitCryptExportKey(repoPath, keyFile string) error {
	_, err := execute.Run(repoPath, "git-crypt", "export-key", keyFile)
	if err != nil {
		return fmt.Errorf("git-crypt export-key: %w", err)
	}
	return nil
}

// GitCryptUnlock unlocks a git-crypt repository using a key file.
func GitCryptUnlock(repoPath, keyFile string) error {
	_, err := execute.Run(repoPath, "git-crypt", "unlock", keyFile)
	if err != nil {
		return fmt.Errorf("git-crypt unlock: %w", err)
	}
	return nil
}

// IsGitCryptInstalled checks whether git-crypt is available on the system.
func IsGitCryptInstalled() bool {
	_, err := exec.LookPath("git-crypt")
	return err == nil
}

// IsGHInstalled checks whether the GitHub CLI (gh) is available.
func IsGHInstalled() bool {
	_, err := exec.LookPath("gh")
	return err == nil
}
