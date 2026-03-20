package orchestrators

import (
	"bytes"
	"embed"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
)

// noClobberStackFiles lists informational files bundled with the stack that
// are created on first install but should not be recreated if the user
// deletes them.
var noClobberStackFiles = map[string]bool{
	"docker-compose.override.yml.example": true,
}

// writeStackFiles walks an embedded FS and writes files to installPath.
// If overwrite is false, existing files are skipped (no-clobber).
func writeStackFiles(stackFS embed.FS, root, installPath string, overwrite bool) error {
	return fs.WalkDir(stackFS, root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relPath, err := filepath.Rel("files", path)
		if err != nil {
			return err
		}
		if relPath == "." {
			return nil
		}
		targetPath := filepath.Join(installPath, relPath)
		if d.IsDir() {
			return os.MkdirAll(targetPath, permissions.DirectoryPermission)
		}
		if d.Name() == ".gitkeep" {
			return nil
		}
		if !overwrite {
			if _, err := os.Stat(targetPath); err == nil {
				return nil // no-clobber
			}
		}
		data, err := stackFS.ReadFile(path)
		if err != nil {
			return fmt.Errorf("failed to read embedded file %s: %w", path, err)
		}
		return os.WriteFile(targetPath, data, permissions.FilePermission)
	})
}

// updateStackFiles walks an embedded FS, compares with on-disk versions,
// and overwrites any that differ. Returns true if anything changed.
func updateStackFiles(stackFS embed.FS, root, installPath string, dryRun bool) (bool, error) {
	changed := false
	err := fs.WalkDir(stackFS, root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relPath, err := filepath.Rel("files", path)
		if err != nil {
			return err
		}
		if relPath == "." {
			return nil
		}
		targetPath := filepath.Join(installPath, relPath)
		if d.IsDir() {
			if !dryRun {
				return os.MkdirAll(targetPath, permissions.DirectoryPermission)
			}
			return nil
		}
		if d.Name() == ".gitkeep" {
			return nil
		}
		embedded, err := stackFS.ReadFile(path)
		if err != nil {
			return fmt.Errorf("failed to read embedded file %s: %w", path, err)
		}
		existing, readErr := os.ReadFile(targetPath)
		if readErr == nil && bytes.Equal(existing, embedded) {
			return nil // unchanged
		}
		// No-clobber: if the file was deleted by the user, don't recreate it.
		if readErr != nil && noClobberStackFiles[relPath] {
			return nil
		}
		changed = true
		if dryRun {
			if readErr != nil {
				fmt.Printf("  Would create %s\n", relPath)
			} else {
				fmt.Printf("  Would update %s\n", relPath)
			}
			return nil
		}
		if readErr != nil {
			fmt.Printf("  Creating %s\n", relPath)
		} else {
			fmt.Printf("  Updating %s\n", relPath)
		}
		return os.WriteFile(targetPath, embedded, permissions.FilePermission)
	})
	return changed, err
}
