package devmode

import (
	"io"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

func TestIsDevModeSetup(t *testing.T) {
	gotests := []struct {
		name  string
		setup func(t *testing.T, dir string)
		want  bool
	}{
		{
			name: "both files present",
			setup: func(t *testing.T, dir string) {
				if err := os.WriteFile(filepath.Join(dir, "docker-compose.dev.yml"), []byte{}, 0644); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(dir, "Dockerfile.xdebug"), []byte{}, 0644); err != nil {
					t.Fatal(err)
				}
			},
			want: true,
		},
		{
			name: "neither file present",
			setup: func(_ *testing.T, _ string) {
			},
			want: false,
		},
		{
			name: "only docker-compose.dev.yml present",
			setup: func(t *testing.T, dir string) {
				if err := os.WriteFile(filepath.Join(dir, "docker-compose.dev.yml"), []byte{}, 0644); err != nil {
					t.Fatal(err)
				}
			},
			want: false,
		},
		{
			name: "only Dockerfile.xdebug present",
			setup: func(t *testing.T, dir string) {
				if err := os.WriteFile(filepath.Join(dir, "Dockerfile.xdebug"), []byte{}, 0644); err != nil {
					t.Fatal(err)
				}
			},
			want: false,
		},
	}

	for _, tt := range gotests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			tt.setup(t, dir)

			if got := IsDevModeSetup(dir); got != tt.want {
				t.Errorf("IsDevModeSetup(%q) = %v, want %v", dir, got, tt.want)
			}
		})
	}
}

func TestConsolidateUserDir(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink behavior is unreliable on Windows without admin privileges")
	}

	origRun := execute.Run
	defer execute.ResetForTesting()

	execute.Run = func(path, command string, cmdArgs ...string) (string, error) {
		if command != "cp" || len(cmdArgs) < 3 || cmdArgs[0] != "-r" {
			return "", nil
		}
		src, dst := cmdArgs[1], cmdArgs[2]
		return "", copyDir(src, dst)
	}

	t.Run("user directory exists with contents", func(t *testing.T) {
		installDir := t.TempDir()
		codeDir := filepath.Join(installDir, CodeDir)
		sourceDir := filepath.Join(installDir, "extensions")
		targetDir := filepath.Join(codeDir, "user-extensions")

		if err := os.MkdirAll(filepath.Join(sourceDir, "ext1"), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(sourceDir, "ext1", "file.txt"), []byte("source"), 0644); err != nil {
			t.Fatal(err)
		}

		if err := os.MkdirAll(targetDir, 0755); err != nil {
			t.Fatal(err)
		}

		if err := consolidateUserDir(installDir, codeDir, "extensions", "user-extensions"); err != nil {
			t.Fatal(err)
		}

		link, err := os.Readlink(sourceDir)
		if err != nil {
			t.Fatalf("expected a symlink for %s, got error: %v", sourceDir, err)
		}
		if link != filepath.Join(CodeDir, "user-extensions") {
			t.Fatalf("expected symlink target %s, got %s", filepath.Join(CodeDir, "user-extensions"), link)
		}

		copiedPath := filepath.Join(targetDir, "ext1", "file.txt")
		got, err := os.ReadFile(copiedPath)
		if err != nil {
			t.Fatalf("expected copied file at %s, got error: %v", copiedPath, err)
		}
		if string(got) != "source" {
			t.Fatalf("expected copied content 'source', got %q", string(got))
		}
	})

	t.Run("user directory does not exist", func(t *testing.T) {
		installDir := t.TempDir()
		codeDir := filepath.Join(installDir, CodeDir)

		if err := consolidateUserDir(installDir, codeDir, "extensions", "user-extensions"); err != nil {
			t.Fatal(err)
		}

		sourceDir := filepath.Join(installDir, "extensions")
		link, err := os.Readlink(sourceDir)
		if err != nil {
			t.Fatalf("expected symlink, got %v", err)
		}
		if link != filepath.Join(CodeDir, "user-extensions") {
			t.Fatalf("expected symlink target %s, got %s", filepath.Join(CodeDir, "user-extensions"), link)
		}
	})

	t.Run("symlink already exists", func(t *testing.T) {
		installDir := t.TempDir()
		codeDir := filepath.Join(installDir, CodeDir)
		sourceDir := filepath.Join(installDir, "extensions")
		targetDir := filepath.Join(codeDir, "user-extensions")

		if err := os.MkdirAll(targetDir, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(filepath.Join("..", targetDir), sourceDir); err != nil {
			t.Fatal(err)
		}

		if err := consolidateUserDir(installDir, codeDir, "extensions", "user-extensions"); err != nil {
			t.Fatal(err)
		}

		_, err := os.Lstat(sourceDir)
		if err != nil {
			t.Fatalf("expected symlink to remain, got %v", err)
		}
	})

	t.Run("target directory already exists in code dir", func(t *testing.T) {
		installDir := t.TempDir()
		codeDir := filepath.Join(installDir, CodeDir)
		sourceDir := filepath.Join(installDir, "extensions")
		targetDir := filepath.Join(codeDir, "user-extensions")

		if err := os.MkdirAll(filepath.Join(sourceDir, "ext1"), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(sourceDir, "ext1", "file.txt"), []byte("source"), 0644); err != nil {
			t.Fatal(err)
		}

		if err := os.MkdirAll(filepath.Join(targetDir, "ext1"), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(targetDir, "ext1", "file.txt"), []byte("old"), 0644); err != nil {
			t.Fatal(err)
		}

		if err := consolidateUserDir(installDir, codeDir, "extensions", "user-extensions"); err != nil {
			t.Fatal(err)
		}

		copiedPath := filepath.Join(targetDir, "ext1", "file.txt")
		got, err := os.ReadFile(copiedPath)
		if err != nil {
			t.Fatalf("expected copied file at %s, got error: %v", copiedPath, err)
		}
		if string(got) != "source" {
			t.Fatalf("expected replaced content 'source', got %q", string(got))
		}
	})

	execute.Run = origRun
}

func copyDir(src, dst string) error {
	if src == "" || dst == "" {
		return nil
	}
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		dstPath := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(dstPath, info.Mode())
		}
		in, err := os.Open(path)
		if err != nil {
			return err
		}
		defer in.Close()
		out, err := os.OpenFile(dstPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, info.Mode())
		if err != nil {
			return err
		}
		defer out.Close()
		_, err = io.Copy(out, in)
		return err
	})
}
