package devmode

import (
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
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

func TestRestoreUserDirSymlink(t *testing.T) {
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

	t.Run("symlink exists with source content", func(t *testing.T) {
		installDir := t.TempDir()
		codeDir := filepath.Join(installDir, CodeDir)
		sourceDir := filepath.Join(codeDir, "user-extensions")

		// Create user-extensions with content in mediawiki-code/
		if err := os.MkdirAll(filepath.Join(sourceDir, "ext1"), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(sourceDir, "ext1", "file.txt"), []byte("ext content"), 0644); err != nil {
			t.Fatal(err)
		}

		// Create symlink: extensions -> mediawiki-code/user-extensions
		symlinkPath := filepath.Join(installDir, "extensions")
		if err := os.Symlink(filepath.Join(CodeDir, "user-extensions"), symlinkPath); err != nil {
			t.Fatal(err)
		}

		if err := restoreUserDir(installDir, "extensions", "user-extensions"); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		// Should now be a real directory, not a symlink
		info, err := os.Lstat(symlinkPath)
		if err != nil {
			t.Fatalf("expected directory to exist: %v", err)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			t.Error("expected a real directory, got a symlink")
		}
		if !info.IsDir() {
			t.Error("expected a directory")
		}

		// Content should be copied
		got, err := os.ReadFile(filepath.Join(symlinkPath, "ext1", "file.txt"))
		if err != nil {
			t.Fatalf("expected file to exist: %v", err)
		}
		if string(got) != "ext content" {
			t.Errorf("expected 'ext content', got %q", string(got))
		}
	})

	t.Run("regular directory exists", func(t *testing.T) {
		installDir := t.TempDir()
		dirPath := filepath.Join(installDir, "extensions")

		// Create a real directory with content
		if err := os.MkdirAll(filepath.Join(dirPath, "ext1"), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dirPath, "ext1", "file.txt"), []byte("existing"), 0644); err != nil {
			t.Fatal(err)
		}

		if err := restoreUserDir(installDir, "extensions", "user-extensions"); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		// Should remain a real directory, unchanged
		info, err := os.Lstat(dirPath)
		if err != nil {
			t.Fatalf("expected directory to exist: %v", err)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			t.Error("should still be a real directory")
		}

		got, err := os.ReadFile(filepath.Join(dirPath, "ext1", "file.txt"))
		if err != nil {
			t.Fatalf("expected file to exist: %v", err)
		}
		if string(got) != "existing" {
			t.Errorf("expected 'existing', got %q", string(got))
		}
	})

	t.Run("directory does not exist", func(t *testing.T) {
		installDir := t.TempDir()
		dirPath := filepath.Join(installDir, "extensions")

		if err := restoreUserDir(installDir, "extensions", "user-extensions"); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		info, err := os.Stat(dirPath)
		if err != nil {
			t.Fatalf("expected directory to be created: %v", err)
		}
		if !info.IsDir() {
			t.Error("expected a directory to be created")
		}
	})

	t.Run("symlink exists but no source directory", func(t *testing.T) {
		installDir := t.TempDir()
		symlinkPath := filepath.Join(installDir, "extensions")

		// Create symlink pointing to non-existent target
		if err := os.Symlink(filepath.Join(CodeDir, "user-extensions"), symlinkPath); err != nil {
			t.Fatal(err)
		}

		if err := restoreUserDir(installDir, "extensions", "user-extensions"); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		// Should now be a real (empty) directory
		info, err := os.Lstat(symlinkPath)
		if err != nil {
			t.Fatalf("expected directory to exist: %v", err)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			t.Error("expected a real directory, got a symlink")
		}
		if !info.IsDir() {
			t.Error("expected a directory")
		}
	})

	execute.Run = origRun
}

func TestWriteIDEConfigs(t *testing.T) {
	t.Run("creates vscode and phpstorm configs", func(t *testing.T) {
		installDir := t.TempDir()
		envPath := filepath.Join(installDir, ".env")
		if err := os.WriteFile(envPath, []byte("MW_SITE_FQDN=example.com\nHTTPS_PORT=8443\n"), 0644); err != nil {
			t.Fatal(err)
		}

		if err := WriteIDEConfigs(installDir); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		// Check VSCode launch.json exists
		launchPath := filepath.Join(installDir, VSCodeDir, "launch.json")
		if _, err := os.Stat(launchPath); err != nil {
			t.Fatalf("expected launch.json to exist: %v", err)
		}

		launchContent, err := os.ReadFile(launchPath)
		if err != nil {
			t.Fatal(err)
		}
		// Verify xdebug port is in the config
		if !strings.Contains(string(launchContent), "9003") {
			t.Error("expected xdebug port 9003 in launch.json")
		}

		// Check PHPStorm php.xml exists with correct host/port
		phpXMLPath := filepath.Join(installDir, PHPStormDir, "php.xml")
		phpXML, err := os.ReadFile(phpXMLPath)
		if err != nil {
			t.Fatalf("expected php.xml to exist: %v", err)
		}
		if !strings.Contains(string(phpXML), `host="example.com"`) {
			t.Error("expected host=example.com in php.xml")
		}
		if !strings.Contains(string(phpXML), `port="8443"`) {
			t.Error("expected port=8443 in php.xml")
		}

		// Check PHPStorm run configuration exists
		runConfigPath := filepath.Join(installDir, PHPStormRunConfDir, "Listen_for_Xdebug.xml")
		if _, err := os.Stat(runConfigPath); err != nil {
			t.Fatalf("expected Listen_for_Xdebug.xml to exist: %v", err)
		}
	})

	t.Run("overwrites existing files", func(t *testing.T) {
		installDir := t.TempDir()
		envPath := filepath.Join(installDir, ".env")
		if err := os.WriteFile(envPath, []byte("MW_SITE_FQDN=new.example.com\nHTTPS_PORT=9443\n"), 0644); err != nil {
			t.Fatal(err)
		}

		// Create pre-existing files
		vscodeDir := filepath.Join(installDir, VSCodeDir)
		if err := os.MkdirAll(vscodeDir, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(vscodeDir, "launch.json"), []byte("old content"), 0644); err != nil {
			t.Fatal(err)
		}

		if err := WriteIDEConfigs(installDir); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		// Verify it was overwritten (not the old content)
		got, err := os.ReadFile(filepath.Join(vscodeDir, "launch.json"))
		if err != nil {
			t.Fatal(err)
		}
		if string(got) == "old content" {
			t.Error("expected launch.json to be overwritten")
		}
		if !strings.Contains(string(got), "9003") {
			t.Error("expected xdebug port 9003 in overwritten launch.json")
		}

		// Check php.xml has the new host/port
		phpXML, err := os.ReadFile(filepath.Join(installDir, PHPStormDir, "php.xml"))
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(phpXML), `host="new.example.com"`) {
			t.Errorf("expected host=new.example.com in php.xml, got:\n%s", string(phpXML))
		}
		if !strings.Contains(string(phpXML), `port="9443"`) {
			t.Errorf("expected port=9443 in php.xml, got:\n%s", string(phpXML))
		}
	})

	t.Run("defaults to localhost and 443 without env", func(t *testing.T) {
		installDir := t.TempDir()
		// No .env file at all

		if err := WriteIDEConfigs(installDir); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		phpXML, err := os.ReadFile(filepath.Join(installDir, PHPStormDir, "php.xml"))
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(phpXML), `host="localhost"`) {
			t.Errorf("expected default host=localhost, got:\n%s", string(phpXML))
		}
		if !strings.Contains(string(phpXML), `port="443"`) {
			t.Errorf("expected default port=443, got:\n%s", string(phpXML))
		}
	})

	t.Run("correct xdebug port in vscode config", func(t *testing.T) {
		installDir := t.TempDir()
		envPath := filepath.Join(installDir, ".env")
		if err := os.WriteFile(envPath, []byte(""), 0644); err != nil {
			t.Fatal(err)
		}

		if err := WriteIDEConfigs(installDir); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		launchContent, err := os.ReadFile(filepath.Join(installDir, VSCodeDir, "launch.json"))
		if err != nil {
			t.Fatal(err)
		}
		// Port 9003 is the standard xdebug port
		if !strings.Contains(string(launchContent), `"port": 9003`) {
			t.Errorf("expected port 9003 in VSCode config, got:\n%s", string(launchContent))
		}
	})
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
