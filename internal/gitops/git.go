package gitops

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

// InitRepo initializes a new git repository at the given path with "main"
// as the default branch.
func InitRepo(path string) error {
	_, err := execute.Run(path, "git", "init", "-b", "main")
	if err != nil {
		return fmt.Errorf("git init: %w", err)
	}
	return nil
}

// Clone clones a repository into the given path. The path must already
// exist; the repo contents are cloned directly into it.
func Clone(repoURL, path string) error {
	_, err := execute.Run(path, "git", "clone", repoURL, ".")
	if err != nil {
		return fmt.Errorf("git clone: %w", err)
	}
	return nil
}

// Pull runs git pull in the given path.
func Pull(path string) error {
	_, err := execute.Run(path, "git", "pull")
	if err != nil {
		return fmt.Errorf("git pull: %w", err)
	}
	return nil
}

// AddRemote adds a named remote to the repository.
func AddRemote(path, name, url string) error {
	_, err := execute.Run(path, "git", "remote", "add", name, url)
	if err != nil {
		return fmt.Errorf("git remote add %s: %w", name, err)
	}
	return nil
}

// IsRemoteEmpty returns true if the remote has no refs (no commits).
func IsRemoteEmpty(path, remote string) (bool, error) {
	output, err := execute.Run(path, "git", "ls-remote", remote)
	if err != nil {
		return false, fmt.Errorf("git ls-remote %s: %w", remote, err)
	}
	return strings.TrimSpace(output) == "", nil
}

// Push pushes the given branch to the remote.
func Push(path, branch string) error {
	_, err := execute.Run(path, "git", "push", "-u", "origin", branch)
	if err != nil {
		return fmt.Errorf("git push: %w", err)
	}
	return nil
}

// ForcePush force-pushes the given branch to the remote, overwriting
// remote history.
func ForcePush(path, branch string) error {
	_, err := execute.Run(path, "git", "push", "--force", "-u", "origin", branch)
	if err != nil {
		return fmt.Errorf("git push --force: %w", err)
	}
	return nil
}

// Add stages specific files.
func Add(path string, files ...string) error {
	args := append([]string{"add"}, files...)
	_, err := execute.Run(path, "git", args...)
	if err != nil {
		return fmt.Errorf("git add: %w", err)
	}
	return nil
}

// AddAll stages all changes to tracked files and new untracked files.
func AddAll(path string) error {
	_, err := execute.Run(path, "git", "add", "-A")
	if err != nil {
		return fmt.Errorf("git add -A: %w", err)
	}
	return nil
}

// Commit creates a commit with the given message. Returns the short
// commit hash.
func Commit(path, message string) (string, error) {
	_, err := execute.Run(path, "git", "commit", "-m", message)
	if err != nil {
		return "", fmt.Errorf("git commit: %w", err)
	}
	hash, err := execute.Run(path, "git", "rev-parse", "--short", "HEAD")
	if err != nil {
		return "", fmt.Errorf("git rev-parse: %w", err)
	}
	return strings.TrimSpace(hash), nil
}

// CreateBranch creates and checks out a new branch.
func CreateBranch(path, name string) error {
	_, err := execute.Run(path, "git", "checkout", "-b", name)
	if err != nil {
		return fmt.Errorf("git checkout -b %s: %w", name, err)
	}
	return nil
}

// CheckoutMain checks out the main branch.
func CheckoutMain(path string) error {
	_, err := execute.Run(path, "git", "checkout", "main")
	if err != nil {
		return fmt.Errorf("git checkout main: %w", err)
	}
	return nil
}

// SubmoduleUpdate runs git submodule update --init --recursive.
func SubmoduleUpdate(path string) error {
	_, err := execute.Run(path, "git", "submodule", "update", "--init", "--recursive")
	if err != nil {
		return fmt.Errorf("git submodule update: %w", err)
	}
	return nil
}

// SubmoduleAdd adds a git submodule at the given relative path.
func SubmoduleAdd(repoPath, submoduleURL, relativePath string) error {
	_, err := execute.Run(repoPath, "git", "submodule", "add", submoduleURL, relativePath)
	if err != nil {
		return fmt.Errorf("git submodule add %s: %w", relativePath, err)
	}
	return nil
}

// HasUncommittedChanges checks whether the working tree has uncommitted
// changes. Returns true if there are changes, along with a list of
// modified file paths.
func HasUncommittedChanges(path string) (bool, []string, error) {
	output, err := execute.Run(path, "git", "status", "--porcelain")
	if err != nil {
		return false, nil, fmt.Errorf("git status: %w", err)
	}
	output = strings.TrimSpace(output)
	if output == "" {
		return false, nil, nil
	}
	var files []string
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		// porcelain format: "XY filename"
		if len(line) > 3 {
			files = append(files, line[3:])
		} else {
			files = append(files, line)
		}
	}
	return true, files, nil
}

// CurrentCommitHash returns the full commit hash of HEAD.
func CurrentCommitHash(path string) (string, error) {
	output, err := execute.Run(path, "git", "rev-parse", "HEAD")
	if err != nil {
		return "", fmt.Errorf("git rev-parse HEAD: %w", err)
	}
	return strings.TrimSpace(output), nil
}

// Fetch runs git fetch without merging.
func Fetch(path string) error {
	_, err := execute.Run(path, "git", "fetch")
	if err != nil {
		return fmt.Errorf("git fetch: %w", err)
	}
	return nil
}

// AheadBehind returns how many commits the current branch is ahead of
// and behind the remote tracking branch.
func AheadBehind(path string) (ahead, behind int, err error) {
	output, err := execute.Run(path, "git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
	if err != nil {
		return 0, 0, fmt.Errorf("git rev-list: %w", err)
	}
	_, scanErr := fmt.Sscanf(strings.TrimSpace(output), "%d\t%d", &ahead, &behind)
	if scanErr != nil {
		return 0, 0, fmt.Errorf("parsing ahead/behind: %w", scanErr)
	}
	return ahead, behind, nil
}

// DiffNameOnly returns the list of files that differ between the
// working tree and the given ref (e.g., "origin/main").
func DiffNameOnly(path, ref string) ([]string, error) {
	output, err := execute.Run(path, "git", "diff", "--name-only", ref)
	if err != nil {
		return nil, fmt.Errorf("git diff --name-only: %w", err)
	}
	output = strings.TrimSpace(output)
	if output == "" {
		return nil, nil
	}
	return strings.Split(output, "\n"), nil
}

// CheckoutHead checks out all tracked files from HEAD into the working tree
// without overwriting untracked files.
func CheckoutHead(path string) error {
	_, err := execute.Run(path, "git", "checkout", "HEAD", "--", ".")
	if err != nil {
		return fmt.Errorf("git checkout HEAD: %w", err)
	}
	return nil
}

// CreatePR uses the gh CLI to create a pull request.
func CreatePR(path, title, body string) (string, error) {
	output, err := execute.Run(path, "gh", "pr", "create", "--title", title, "--body", body)
	if err != nil {
		return "", fmt.Errorf("gh pr create: %w", err)
	}
	return strings.TrimSpace(output), nil
}
