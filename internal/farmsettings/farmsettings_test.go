package farmsettings

import (
	"os"
	"path/filepath"
	"testing"
)

func TestValidateWikiID(t *testing.T) {
	tests := []struct {
		name    string
		wikiID  string
		wantErr bool
	}{
		{"valid simple", "mywiki", false},
		{"valid with underscore", "my_wiki", false},
		{"valid numeric", "wiki123", false},
		{"reserved settings", "settings", true},
		{"reserved images", "images", true},
		{"reserved w", "w", true},
		{"reserved wiki", "wiki", true},
		{"contains hyphen", "my-wiki", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateWikiID(tt.wikiID)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateWikiID(%q) error = %v, wantErr %v", tt.wikiID, err, tt.wantErr)
			}
		})
	}
}

func TestGenerateAndReadWikisYaml(t *testing.T) {
	dir := t.TempDir()
	filePath := filepath.Join(dir, "wikis.yaml")

	absPath, err := GenerateWikisYaml(filePath, "testwiki", "example.com", "Test Wiki")
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}
	if absPath == "" {
		t.Fatal("GenerateWikisYaml() returned empty path")
	}

	ids, serverNames, paths, err := ReadWikisYaml(filePath)
	if err != nil {
		t.Fatalf("ReadWikisYaml() error = %v", err)
	}

	if len(ids) != 1 || ids[0] != "testwiki" {
		t.Errorf("expected ids=[testwiki], got %v", ids)
	}
	if len(serverNames) != 1 || serverNames[0] != "example.com" {
		t.Errorf("expected serverNames=[example.com], got %v", serverNames)
	}
	if len(paths) != 1 || paths[0] != "/" {
		t.Errorf("expected paths=[/], got %v", paths)
	}
}

func TestGenerateWikisYamlDefaultSiteName(t *testing.T) {
	dir := t.TempDir()
	filePath := filepath.Join(dir, "wikis.yaml")

	_, err := GenerateWikisYaml(filePath, "mywiki", "example.com", "")
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	// Read back and verify siteName defaults to wikiID
	data, err := os.ReadFile(filePath)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	content := string(data)
	if !contains(content, "name: mywiki") {
		t.Errorf("expected siteName to default to wikiID, got:\n%s", content)
	}
}

func TestReadWikisYamlWithPath(t *testing.T) {
	dir := t.TempDir()
	filePath := filepath.Join(dir, "wikis.yaml")

	// Generate with URL containing a path
	_, err := GenerateWikisYaml(filePath, "testwiki", "example.com/mypath", "Test")
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	ids, serverNames, paths, err := ReadWikisYaml(filePath)
	if err != nil {
		t.Fatalf("ReadWikisYaml() error = %v", err)
	}

	if ids[0] != "testwiki" {
		t.Errorf("expected id=testwiki, got %s", ids[0])
	}
	if serverNames[0] != "example.com" {
		t.Errorf("expected serverName=example.com, got %s", serverNames[0])
	}
	if paths[0] != "/mypath" {
		t.Errorf("expected path=/mypath, got %s", paths[0])
	}
}

func TestAddAndRemoveWiki(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	filePath := filepath.Join(configDir, "wikis.yaml")

	// Generate initial wiki
	_, err := GenerateWikisYaml(filePath, "wiki1", "example.com", "Wiki 1")
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	// Add a second wiki
	err = AddWiki("wiki2", dir, "other.com", "", "Wiki 2")
	if err != nil {
		t.Fatalf("AddWiki() error = %v", err)
	}

	ids, _, _, err := ReadWikisYaml(filePath)
	if err != nil {
		t.Fatalf("ReadWikisYaml() error = %v", err)
	}
	if len(ids) != 2 {
		t.Fatalf("expected 2 wikis, got %d", len(ids))
	}
	if ids[0] != "wiki1" || ids[1] != "wiki2" {
		t.Errorf("expected [wiki1, wiki2], got %v", ids)
	}

	// Remove first wiki
	err = RemoveWiki("wiki1", dir)
	if err != nil {
		t.Fatalf("RemoveWiki() error = %v", err)
	}

	ids, _, _, err = ReadWikisYaml(filePath)
	if err != nil {
		t.Fatalf("ReadWikisYaml() error = %v", err)
	}
	if len(ids) != 1 || ids[0] != "wiki2" {
		t.Errorf("expected [wiki2], got %v", ids)
	}
}

func TestRemoveLastWikiFails(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	filePath := filepath.Join(configDir, "wikis.yaml")

	_, err := GenerateWikisYaml(filePath, "onlywiki", "example.com", "Only")
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	err = RemoveWiki("onlywiki", dir)
	if err == nil {
		t.Fatal("expected error when removing last wiki, got nil")
	}
}

func TestWikiIDExists(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	filePath := filepath.Join(configDir, "wikis.yaml")

	_, err := GenerateWikisYaml(filePath, "testwiki", "example.com", "Test")
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	exists, err := WikiIDExists(dir, "testwiki")
	if err != nil {
		t.Fatalf("WikiIDExists() error = %v", err)
	}
	if !exists {
		t.Error("expected testwiki to exist")
	}

	exists, err = WikiIDExists(dir, "nonexistent")
	if err != nil {
		t.Fatalf("WikiIDExists() error = %v", err)
	}
	if exists {
		t.Error("expected nonexistent to not exist")
	}
}

func TestWikiIDExistsNoFile(t *testing.T) {
	dir := t.TempDir()

	exists, err := WikiIDExists(dir, "anywiki")
	if err != nil {
		t.Fatalf("WikiIDExists() error = %v", err)
	}
	if exists {
		t.Error("expected false when wikis.yaml doesn't exist")
	}
}

func TestWikiUrlExists(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	filePath := filepath.Join(configDir, "wikis.yaml")

	_, err := GenerateWikisYaml(filePath, "testwiki", "example.com/wiki", "Test")
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	exists, err := WikiUrlExists(dir, "example.com", "wiki")
	if err != nil {
		t.Fatalf("WikiUrlExists() error = %v", err)
	}
	if !exists {
		t.Error("expected URL to exist")
	}

	exists, err = WikiUrlExists(dir, "other.com", "wiki")
	if err != nil {
		t.Fatalf("WikiUrlExists() error = %v", err)
	}
	if exists {
		t.Error("expected other.com URL to not exist")
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsSubstring(s, substr))
}

func containsSubstring(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
