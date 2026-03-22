package gitops

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolveToInstallPath(t *testing.T) {
	installDir := t.TempDir()

	// Create a nested file inside the install dir.
	subdir := filepath.Join(installDir, "config", "settings")
	if err := os.MkdirAll(subdir, 0755); err != nil {
		t.Fatalf("creating subdir: %v", err)
	}
	testFile := filepath.Join(subdir, "test.php")
	if err := os.WriteFile(testFile, []byte("<?php\n"), 0644); err != nil {
		t.Fatalf("writing test file: %v", err)
	}

	t.Run("absolute path inside install dir", func(t *testing.T) {
		rel, err := resolveToInstallPath(installDir, testFile)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		expected := filepath.Join("config", "settings", "test.php")
		if rel != expected {
			t.Errorf("got %q, want %q", rel, expected)
		}
	})

	t.Run("relative path from install dir", func(t *testing.T) {
		// Save and restore working directory.
		orig, err := os.Getwd()
		if err != nil {
			t.Fatalf("getting cwd: %v", err)
		}
		t.Cleanup(func() {
			if err := os.Chdir(orig); err != nil {
				t.Fatalf("restoring cwd: %v", err)
			}
		})

		if err := os.Chdir(subdir); err != nil {
			t.Fatalf("chdir: %v", err)
		}

		rel, err := resolveToInstallPath(installDir, "test.php")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		expected := filepath.Join("config", "settings", "test.php")
		if rel != expected {
			t.Errorf("got %q, want %q", rel, expected)
		}
	})

	t.Run("file outside install dir", func(t *testing.T) {
		outsideFile := filepath.Join(t.TempDir(), "outside.txt")
		if err := os.WriteFile(outsideFile, []byte("x"), 0644); err != nil {
			t.Fatalf("writing file: %v", err)
		}

		_, err := resolveToInstallPath(installDir, outsideFile)
		if err == nil {
			t.Fatal("expected error for file outside install dir")
		}
	})

	t.Run("nonexistent file", func(t *testing.T) {
		_, err := resolveToInstallPath(installDir, filepath.Join(installDir, "nope.txt"))
		if err == nil {
			t.Fatal("expected error for nonexistent file")
		}
	})
}
