package farmsettings

import (
	"os"
	"path/filepath"
	"strings"
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

	absPath, err := GenerateWikisYaml(filePath, "testwiki", "example.com", "Test Wiki", false)
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

	_, err := GenerateWikisYaml(filePath, "mywiki", "example.com", "", false)
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	// Read back and verify siteName defaults to wikiID
	data, err := os.ReadFile(filePath)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	content := string(data)
	if !strings.Contains(content, "name: mywiki") {
		t.Errorf("expected siteName to default to wikiID, got:\n%s", content)
	}
}

func TestReadWikisYamlWithPath(t *testing.T) {
	dir := t.TempDir()
	filePath := filepath.Join(dir, "wikis.yaml")

	// Generate with URL containing a path
	_, err := GenerateWikisYaml(filePath, "testwiki", "example.com/mypath", "Test", false)
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
	_, err := GenerateWikisYaml(filePath, "wiki1", "example.com", "Wiki 1", false)
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	// Add a second wiki
	err = AddWiki("wiki2", dir, "other.com", "", "Wiki 2", false)
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

	_, err := GenerateWikisYaml(filePath, "onlywiki", "example.com", "Only", false)
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

	_, err := GenerateWikisYaml(filePath, "testwiki", "example.com", "Test", false)
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

	_, err := GenerateWikisYaml(filePath, "testwiki", "example.com/wiki", "Test", false)
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

func TestGenerateWikisYamlWithSitemap(t *testing.T) {
	dir := t.TempDir()
	filePath := filepath.Join(dir, "wikis.yaml")

	_, err := GenerateWikisYaml(filePath, "mywiki", "example.com", "My Wiki", true)
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	data, err := os.ReadFile(filePath)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	content := string(data)
	if !strings.Contains(content, "sitemap: true") {
		t.Errorf("expected sitemap: true in output, got:\n%s", content)
	}
}

func TestGenerateWikisYamlWithoutSitemap(t *testing.T) {
	dir := t.TempDir()
	filePath := filepath.Join(dir, "wikis.yaml")

	_, err := GenerateWikisYaml(filePath, "mywiki", "example.com", "My Wiki", false)
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	data, err := os.ReadFile(filePath)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	content := string(data)
	if strings.Contains(content, "sitemap") {
		t.Errorf("expected no sitemap field in output, got:\n%s", content)
	}
}

func TestAddWikiWithSitemap(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	filePath := filepath.Join(configDir, "wikis.yaml")

	_, err := GenerateWikisYaml(filePath, "wiki1", "example.com", "Wiki 1", false)
	if err != nil {
		t.Fatalf("GenerateWikisYaml() error = %v", err)
	}

	err = AddWiki("wiki2", dir, "other.com", "", "Wiki 2", true)
	if err != nil {
		t.Fatalf("AddWiki() error = %v", err)
	}

	data, err := os.ReadFile(filePath)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	content := string(data)

	// wiki1 should not have sitemap field, wiki2 should have sitemap: true
	lines := strings.Split(content, "\n")
	wiki1HasSitemap := false
	wiki2HasSitemap := false
	currentWiki := ""
	for _, line := range lines {
		if strings.Contains(line, "id: wiki1") {
			currentWiki = "wiki1"
		} else if strings.Contains(line, "id: wiki2") {
			currentWiki = "wiki2"
		}
		if strings.Contains(line, "sitemap: true") {
			if currentWiki == "wiki1" {
				wiki1HasSitemap = true
			} else if currentWiki == "wiki2" {
				wiki2HasSitemap = true
			}
		}
	}

	if wiki1HasSitemap {
		t.Error("wiki1 should not have sitemap field")
	}
	if !wiki2HasSitemap {
		t.Error("wiki2 should have sitemap: true")
	}
}
