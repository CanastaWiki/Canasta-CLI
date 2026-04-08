package upgrade

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
)

func TestRemoveSkipBinaryAsHex(t *testing.T) {
	tests := []struct {
		name        string
		content     string // file content; empty string means don't create the file
		wantChanged bool
		wantContent string // expected content after migration (ignored if !wantChanged)
	}{
		{
			name:        "missing file",
			content:     "",
			wantChanged: false,
		},
		{
			name:        "no skip-binary-as-hex",
			content:     "[mysqld]\nmax_connections=100\n",
			wantChanged: false,
		},
		{
			name:        "in mysql section preserved",
			content:     "[mysql]\nskip-binary-as-hex = true\n",
			wantChanged: false,
		},
		{
			name:        "in client section removed",
			content:     "[client]\nskip-binary-as-hex = true\nport=3306\n",
			wantChanged: true,
			wantContent: "[client]\nport=3306\n",
		},
		{
			name:        "in both sections only client removed",
			content:     "[mysql]\nskip-binary-as-hex = true\n[client]\nskip-binary-as-hex = true\n",
			wantChanged: true,
			wantContent: "[mysql]\nskip-binary-as-hex = true\n[client]\n",
		},
		{
			name:        "before any section header removed",
			content:     "skip-binary-as-hex = true\n[mysql]\nport=3306\n",
			wantChanged: true,
			wantContent: "[mysql]\nport=3306\n",
		},
		{
			name:        "in mysqld section removed",
			content:     "[mysqld]\nskip-binary-as-hex = true\nmax_connections=100\n",
			wantChanged: true,
			wantContent: "[mysqld]\nmax_connections=100\n",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmpDir := t.TempDir()
			filePath := filepath.Join(tmpDir, "my.cnf")

			if tt.content != "" {
				if err := os.WriteFile(filePath, []byte(tt.content), 0644); err != nil {
					t.Fatal(err)
				}
			}

			changed, err := removeSkipBinaryAsHex(tmpDir, false)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if changed != tt.wantChanged {
				t.Errorf("changed = %v, want %v", changed, tt.wantChanged)
			}

			if tt.wantChanged {
				got, err := os.ReadFile(filePath)
				if err != nil {
					t.Fatal("file should still exist after migration")
				}
				if string(got) != tt.wantContent {
					t.Errorf("content = %q, want %q", string(got), tt.wantContent)
				}
			}
		})
	}
}

func TestRemoveSkipBinaryAsHexDryRun(t *testing.T) {
	tmpDir := t.TempDir()
	filePath := filepath.Join(tmpDir, "my.cnf")
	content := "[client]\nskip-binary-as-hex = true\n"
	if err := os.WriteFile(filePath, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := removeSkipBinaryAsHex(tmpDir, true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Error("dry run should report changed = true")
	}

	// File should be unchanged after dry run
	got, err := os.ReadFile(filePath)
	if err != nil {
		t.Fatal("file should still exist after dry run")
	}
	if string(got) != content {
		t.Errorf("dry run should not modify file, got %q", string(got))
	}
}

func TestRemoveEmptyComposerLocal(t *testing.T) {
	tests := []struct {
		name        string
		content     string // file content; empty string means don't create the file
		wantChanged bool
		wantRemoved bool // whether the file should be deleted
	}{
		{
			name:        "missing file",
			content:     "",
			wantChanged: false,
			wantRemoved: false,
		},
		{
			name:        "empty include array",
			content:     `{"extra":{"merge-plugin":{"include":[]}}}`,
			wantChanged: true,
			wantRemoved: true,
		},
		{
			name:        "missing include key",
			content:     `{"extra":{"merge-plugin":{}}}`,
			wantChanged: true,
			wantRemoved: true,
		},
		{
			name:        "missing merge-plugin key",
			content:     `{"extra":{}}`,
			wantChanged: true,
			wantRemoved: true,
		},
		{
			name:        "empty object",
			content:     `{}`,
			wantChanged: true,
			wantRemoved: true,
		},
		{
			name:        "non-empty include array",
			content:     `{"extra":{"merge-plugin":{"include":["extensions/SemanticMediaWiki/composer.json"]}}}`,
			wantChanged: false,
			wantRemoved: false,
		},
		{
			name:        "invalid JSON",
			content:     `not valid json`,
			wantChanged: false,
			wantRemoved: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmpDir := t.TempDir()
			configDir := filepath.Join(tmpDir, "config")
			if err := os.MkdirAll(configDir, 0755); err != nil {
				t.Fatal(err)
			}

			filePath := filepath.Join(configDir, "composer.local.json")
			if tt.content != "" {
				if err := os.WriteFile(filePath, []byte(tt.content), 0644); err != nil {
					t.Fatal(err)
				}
			}

			changed, err := removeEmptyComposerLocal(tmpDir, false)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if changed != tt.wantChanged {
				t.Errorf("changed = %v, want %v", changed, tt.wantChanged)
			}

			_, statErr := os.Stat(filePath)
			fileExists := statErr == nil
			if tt.wantRemoved && fileExists {
				t.Error("file should have been removed but still exists")
			}
			if !tt.wantRemoved && tt.content != "" && !fileExists {
				t.Error("file should not have been removed but is missing")
			}
		})
	}
}

func TestRemoveEmptyComposerLocalDryRun(t *testing.T) {
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}

	filePath := filepath.Join(configDir, "composer.local.json")
	content := `{"extra":{"merge-plugin":{"include":[]}}}`
	if err := os.WriteFile(filePath, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := removeEmptyComposerLocal(tmpDir, true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Error("dry run should report changed = true")
	}

	// File should still exist after dry run
	if _, err := os.Stat(filePath); err != nil {
		t.Error("file should still exist after dry run")
	}
}

func TestBackfillCanastaImage(t *testing.T) {
	tests := []struct {
		name        string
		envContent  string
		wantChanged bool
	}{
		{
			name:        "missing CANASTA_IMAGE",
			envContent:  "MW_SITE_SERVER=https://localhost\nMYSQL_PASSWORD=secret\n",
			wantChanged: true,
		},
		{
			name:        "outdated version",
			envContent:  "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.3.1\nMYSQL_PASSWORD=secret\n",
			wantChanged: true,
		},
		{
			name:        "already current",
			envContent:  "CANASTA_IMAGE=" + canasta.GetDefaultImage() + "\nMYSQL_PASSWORD=secret\n",
			wantChanged: false,
		},
		{
			name:        "empty value",
			envContent:  "CANASTA_IMAGE=\nMYSQL_PASSWORD=secret\n",
			wantChanged: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmpDir := t.TempDir()
			envPath := filepath.Join(tmpDir, ".env")
			if err := os.WriteFile(envPath, []byte(tt.envContent), 0644); err != nil {
				t.Fatal(err)
			}

			changed, err := backfillCanastaImage(tmpDir, false)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if changed != tt.wantChanged {
				t.Errorf("changed = %v, want %v", changed, tt.wantChanged)
			}

			if tt.wantChanged {
				got, err := os.ReadFile(envPath)
				if err != nil {
					t.Fatal(err)
				}
				if !strings.Contains(string(got), "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:") {
					t.Errorf("expected CANASTA_IMAGE to be set, got:\n%s", string(got))
				}
			}
		})
	}
}

func TestBackfillCanastaImageDryRun(t *testing.T) {
	tmpDir := t.TempDir()
	envPath := filepath.Join(tmpDir, ".env")
	content := "MW_SITE_SERVER=https://localhost\n"
	if err := os.WriteFile(envPath, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := backfillCanastaImage(tmpDir, true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Error("dry run should report changed = true")
	}

	// File should be unchanged after dry run
	got, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != content {
		t.Errorf("dry run should not modify file, got %q", string(got))
	}
}

func TestRemoveEmptyComposerLocalPreservesPopulated(t *testing.T) {
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Write a realistic composer.local.json with bundled extension entries
	data := map[string]interface{}{
		"extra": map[string]interface{}{
			"merge-plugin": map[string]interface{}{
				"include": []string{
					"extensions/SemanticMediaWiki/composer.json",
					"extensions/Maps/composer.json",
				},
			},
		},
	}
	content, _ := json.MarshalIndent(data, "", "    ")

	filePath := filepath.Join(configDir, "composer.local.json")
	if err := os.WriteFile(filePath, content, 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := removeEmptyComposerLocal(tmpDir, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if changed {
		t.Error("should not change a populated composer.local.json")
	}

	// Verify file still exists with same content
	got, err := os.ReadFile(filePath)
	if err != nil {
		t.Fatal("file should still exist")
	}
	if string(got) != string(content) {
		t.Error("file content should be unchanged")
	}
}

func writeWikisYAML(t *testing.T, installPath string, wikiIDs ...string) {
	t.Helper()

	configDir := filepath.Join(installPath, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}

	var b strings.Builder
	b.WriteString("wikis:\n")
	for _, id := range wikiIDs {
		b.WriteString("  - id: ")
		b.WriteString(id)
		b.WriteString("\n")
		b.WriteString("    url: ")
		b.WriteString(id)
		b.WriteString(".example.org\n")
		b.WriteString("    name: ")
		b.WriteString(id)
		b.WriteString("\n")
	}

	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte(b.String()), 0644); err != nil {
		t.Fatal(err)
	}
}

func TestMigrateDirectoryStructureLegacyLayout(t *testing.T) {
	installPath := t.TempDir()
	writeWikisYAML(t, installPath, "wiki1")

	legacyWikiDir := filepath.Join(installPath, "config", "wiki1")
	if err := os.MkdirAll(legacyWikiDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacyWikiDir, "LocalSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	settingsDir := filepath.Join(installPath, "config", "settings")
	if err := os.MkdirAll(settingsDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(settingsDir, "CommonSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := migrateDirectoryStructure(installPath, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Fatal("expected changed = true")
	}

	if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "wikis", "wiki1", "LocalSettings.php")); err != nil {
		t.Fatalf("expected migrated wiki directory: %v", err)
	}
	if _, err := os.Stat(legacyWikiDir); !os.IsNotExist(err) {
		t.Fatalf("expected legacy wiki directory to be removed, err=%v", err)
	}

	if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "global", "CommonSettings.php")); err != nil {
		t.Fatalf("expected global PHP file to be moved: %v", err)
	}
	if _, err := os.Stat(filepath.Join(settingsDir, "CommonSettings.php")); !os.IsNotExist(err) {
		t.Fatalf("expected legacy global PHP file to be removed, err=%v", err)
	}
}

func TestMigrateDirectoryStructureAlreadyMigratedNoOp(t *testing.T) {
	installPath := t.TempDir()
	writeWikisYAML(t, installPath, "wiki1")

	migratedWikiDir := filepath.Join(installPath, "config", "settings", "wikis", "wiki1")
	if err := os.MkdirAll(migratedWikiDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(migratedWikiDir, "LocalSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	globalDir := filepath.Join(installPath, "config", "settings", "global")
	if err := os.MkdirAll(globalDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(globalDir, "CommonSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	settingsDir := filepath.Join(installPath, "config", "settings")
	if err := os.WriteFile(filepath.Join(settingsDir, "CommonSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := migrateDirectoryStructure(installPath, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if changed {
		t.Fatal("expected changed = false")
	}

	if _, err := os.Stat(filepath.Join(globalDir, "CommonSettings.php")); err != nil {
		t.Fatalf("global PHP file should still exist: %v", err)
	}
	if _, err := os.Stat(filepath.Join(settingsDir, "CommonSettings.php")); err != nil {
		t.Fatalf("legacy PHP file should still exist (skipped, not removed): %v", err)
	}
}

func TestMigrateDirectoryStructureMultipleWikiDirectories(t *testing.T) {
	installPath := t.TempDir()
	writeWikisYAML(t, installPath, "wiki1", "wiki2")

	for _, wiki := range []string{"wiki1", "wiki2"} {
		legacyWikiDir := filepath.Join(installPath, "config", wiki)
		if err := os.MkdirAll(legacyWikiDir, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(legacyWikiDir, "LocalSettings.php"), []byte("<?php"), 0644); err != nil {
			t.Fatal(err)
		}
	}

	changed, err := migrateDirectoryStructure(installPath, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Fatal("expected changed = true")
	}

	for _, wiki := range []string{"wiki1", "wiki2"} {
		if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "wikis", wiki, "LocalSettings.php")); err != nil {
			t.Fatalf("expected migrated wiki %s: %v", wiki, err)
		}
		if _, err := os.Stat(filepath.Join(installPath, "config", wiki)); !os.IsNotExist(err) {
			t.Fatalf("expected legacy wiki %s to be removed, err=%v", wiki, err)
		}
	}
}

func TestMigrateDirectoryStructureDryRun(t *testing.T) {
	installPath := t.TempDir()
	writeWikisYAML(t, installPath, "wiki1")

	legacyWikiDir := filepath.Join(installPath, "config", "wiki1")
	if err := os.MkdirAll(legacyWikiDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacyWikiDir, "LocalSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	settingsDir := filepath.Join(installPath, "config", "settings")
	if err := os.MkdirAll(settingsDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(settingsDir, "CommonSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := migrateDirectoryStructure(installPath, true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Fatal("expected changed = true in dry-run")
	}

	if _, err := os.Stat(filepath.Join(installPath, "config", "wiki1", "LocalSettings.php")); err != nil {
		t.Fatalf("legacy wiki directory should remain in dry-run: %v", err)
	}
	if _, err := os.Stat(filepath.Join(settingsDir, "CommonSettings.php")); err != nil {
		t.Fatalf("legacy global PHP file should remain in dry-run: %v", err)
	}

	if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "wikis", "wiki1")); !os.IsNotExist(err) {
		t.Fatalf("migrated wiki directory should not be created in dry-run, err=%v", err)
	}
	if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "global", "CommonSettings.php")); !os.IsNotExist(err) {
		t.Fatalf("global PHP destination should not be created in dry-run, err=%v", err)
	}
}

func TestMigrateDirectoryStructureMixedState(t *testing.T) {
	installPath := t.TempDir()
	writeWikisYAML(t, installPath, "wiki1", "wiki2", "wiki3")

	legacyWiki1 := filepath.Join(installPath, "config", "wiki1")
	if err := os.MkdirAll(legacyWiki1, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacyWiki1, "LocalSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	migratedWiki2 := filepath.Join(installPath, "config", "settings", "wikis", "wiki2")
	if err := os.MkdirAll(migratedWiki2, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(migratedWiki2, "LocalSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := migrateDirectoryStructure(installPath, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Fatal("expected changed = true")
	}

	if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "wikis", "wiki1", "LocalSettings.php")); err != nil {
		t.Fatalf("expected wiki1 to be migrated: %v", err)
	}
	if _, err := os.Stat(filepath.Join(installPath, "config", "wiki1")); !os.IsNotExist(err) {
		t.Fatalf("expected legacy wiki1 directory to be removed, err=%v", err)
	}

	if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "wikis", "wiki2", "LocalSettings.php")); err != nil {
		t.Fatalf("expected wiki2 to remain migrated: %v", err)
	}

	if _, err := os.Stat(filepath.Join(installPath, "config", "wiki3")); !os.IsNotExist(err) {
		t.Fatalf("expected wiki3 legacy path to remain absent, err=%v", err)
	}
	if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "wikis", "wiki3")); !os.IsNotExist(err) {
		t.Fatalf("expected wiki3 migrated path to remain absent, err=%v", err)
	}
}

func TestMigrateDirectoryStructureGlobalOnly(t *testing.T) {
	installPath := t.TempDir()
	writeWikisYAML(t, installPath, "wiki1")

	settingsDir := filepath.Join(installPath, "config", "settings")
	if err := os.MkdirAll(settingsDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(settingsDir, "Vector.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(settingsDir, "Extensions.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	changed, err := migrateDirectoryStructure(installPath, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Fatal("expected changed = true")
	}

	for _, name := range []string{"Vector.php", "Extensions.php"} {
		if _, err := os.Stat(filepath.Join(installPath, "config", "settings", "global", name)); err != nil {
			t.Fatalf("expected migrated global file %s: %v", name, err)
		}
		if _, err := os.Stat(filepath.Join(settingsDir, name)); !os.IsNotExist(err) {
			t.Fatalf("expected legacy global file %s to be removed, err=%v", name, err)
		}
	}
}
