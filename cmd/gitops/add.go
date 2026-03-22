package gitops

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/gitops"
)

func newAddCmd(instance *config.Installation) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "add [files...]",
		Short: "Stage files for the next gitops push",
		Long: `Explicitly stage files to be included in the next gitops push.
Only staged files will be committed when you run gitops push.
File paths can be relative to the current directory or to the
installation root.`,
		Args: cobra.MinimumNArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			return runAdd(instance.Path, args)
		},
	}
	return cmd
}

// resolveToInstallPath converts a file path (which may be relative to the
// user's current directory) into a path relative to the installation root.
// When requireExists is true the file must exist on disk (used by add);
// when false, deleted-but-tracked files are allowed (used by rm).
func resolveToInstallPath(installPath, file string, requireExists bool) (string, error) {
	// Make the file path absolute based on the current working directory.
	absFile, err := filepath.Abs(file)
	if err != nil {
		return "", fmt.Errorf("resolving path %q: %w", file, err)
	}

	// Resolve symlinks so paths compare correctly (e.g. /var vs /private/var
	// on macOS). When the file doesn't exist we fall back to the absolute
	// path, which is fine for git rm on deleted files.
	if _, statErr := os.Stat(absFile); os.IsNotExist(statErr) {
		if requireExists {
			return "", fmt.Errorf("file not found: %s", absFile)
		}
		// File was deleted — resolve symlinks on the parent directory so
		// the prefix check works (e.g. /var vs /private/var on macOS).
		dir, base := filepath.Split(absFile)
		resolvedDir, evalErr := filepath.EvalSymlinks(dir)
		if evalErr != nil {
			return "", fmt.Errorf("resolving parent directory %q: %w", dir, evalErr)
		}
		absFile = filepath.Join(resolvedDir, base)
	} else {
		absFile, err = filepath.EvalSymlinks(absFile)
		if err != nil {
			return "", fmt.Errorf("resolving path %q: %w", file, err)
		}
	}

	absInstall, err := filepath.EvalSymlinks(installPath)
	if err != nil {
		return "", fmt.Errorf("resolving install path: %w", err)
	}

	// Ensure the file is inside the installation directory.
	rel, err := filepath.Rel(absInstall, absFile)
	if err != nil {
		return "", fmt.Errorf("computing relative path: %w", err)
	}
	if strings.HasPrefix(rel, "..") {
		return "", fmt.Errorf("%q is outside the installation directory %q", file, absInstall)
	}

	return rel, nil
}

func runAdd(installPath string, files []string) error {
	resolved := make([]string, 0, len(files))
	for _, f := range files {
		rel, err := resolveToInstallPath(installPath, f, true)
		if err != nil {
			return err
		}
		resolved = append(resolved, rel)
	}

	if err := gitops.Add(installPath, resolved...); err != nil {
		return err
	}
	for _, f := range resolved {
		fmt.Printf("Staged: %s\n", f)
	}
	return nil
}
