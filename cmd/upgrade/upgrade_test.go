package upgrade

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
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
			name:        "in mysql section",
			content:     "[mysql]\nskip-binary-as-hex = true\n",
			wantChanged: true,
			wantContent: "[mysql]\n",
		},
		{
			name:        "in client section",
			content:     "[client]\nskip-binary-as-hex = true\nport=3306\n",
			wantChanged: true,
			wantContent: "[client]\nport=3306\n",
		},
		{
			name:        "in multiple sections",
			content:     "[mysql]\nskip-binary-as-hex = true\n[client]\nskip-binary-as-hex = true\n",
			wantChanged: true,
			wantContent: "[mysql]\n[client]\n",
		},
		{
			name:        "loose prefixed variant",
			content:     "[mysql]\nloose-skip-binary-as-hex = true\n",
			wantChanged: true,
			wantContent: "[mysql]\n",
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
	content := "[mysql]\nskip-binary-as-hex = true\n"
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
			name: "missing include key",
			content: `{"extra":{"merge-plugin":{}}}`,
			wantChanged: true,
			wantRemoved: true,
		},
		{
			name: "missing merge-plugin key",
			content: `{"extra":{}}`,
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
			name: "non-empty include array",
			content: `{"extra":{"merge-plugin":{"include":["extensions/SemanticMediaWiki/composer.json"]}}}`,
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
