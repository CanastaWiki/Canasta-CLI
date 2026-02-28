package extensionsskins

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
)

func TestValidateName(t *testing.T) {
	constants := Item{CmdName: "extension"}

	tests := []struct {
		name    string
		input   string
		wantErr bool
	}{
		{"simple name", "VisualEditor", false},
		{"underscore", "Semantic_MediaWiki", false},
		{"hyphen", "My-Extension", false},
		{"dot", "Auth.v2", false},
		{"digits", "Extension123", false},
		{"starts with digit", "3DAlloy", false},
		{"empty", "", true},
		{"shell metachar backtick", "ext`id`", true},
		{"shell metachar dollar", "ext$(cmd)", true},
		{"semicolon", "ext;rm -rf /", true},
		{"space", "Visual Editor", true},
		{"single quote", "ext'name", true},
		{"double quote", `ext"name`, true},
		{"slash", "ext/name", true},
		{"starts with hyphen", "-extension", true},
		{"starts with dot", ".hidden", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateName(tt.input, constants)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateName(%q) error = %v, wantErr %v", tt.input, err, tt.wantErr)
			}
		})
	}
}

func TestSlicesContains(t *testing.T) {
	list := []string{"VisualEditor", "Cite", "ParserFunctions"}

	if !slices.Contains(list, "Cite") {
		t.Error("expected slices.Contains to return true for 'Cite'")
	}
	if slices.Contains(list, "Missing") {
		t.Error("expected slices.Contains to return false for 'Missing'")
	}
}

// --- YAML config helpers ---

var extConstants = Item{
	Name:                     "Canasta extension",
	CmdName:                  "extension",
	Plural:                   "extensions",
	RelativeInstallationPath: "extensions",
	PhpCommand:               "wfLoadExtension",
	ExampleNames:             "VisualEditor,Cite,ParserFunctions",
}

var skinConstants = Item{
	Name:                     "Canasta skin",
	CmdName:                  "skin",
	Plural:                   "skins",
	RelativeInstallationPath: "skins",
	PhpCommand:               "wfLoadSkin",
	ExampleNames:             "Timeless",
}

func TestConfigPath(t *testing.T) {
	got := configPath("/srv/canasta/myinstance", "")
	want := "/srv/canasta/myinstance/config/settings/global/main.yaml"
	if got != want {
		t.Errorf("configPath global = %q, want %q", got, want)
	}

	got = configPath("/srv/canasta/myinstance", "docs")
	want = "/srv/canasta/myinstance/config/settings/wikis/docs/main.yaml"
	if got != want {
		t.Errorf("configPath per-wiki = %q, want %q", got, want)
	}
}

func TestReadConfigEmpty(t *testing.T) {
	cfg, err := readConfig("/nonexistent/path/main.yaml")
	if err != nil {
		t.Fatalf("readConfig on missing file: %v", err)
	}
	if len(cfg.Extensions) != 0 || len(cfg.Skins) != 0 {
		t.Errorf("expected empty config, got %+v", cfg)
	}
}

func TestWriteAndReadConfig(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "main.yaml")

	cfg := configYAML{
		Extensions: []string{"Cite", "VisualEditor"},
		Skins:      []string{"Vector"},
	}
	if err := writeConfig(path, cfg); err != nil {
		t.Fatalf("writeConfig: %v", err)
	}

	got, err := readConfig(path)
	if err != nil {
		t.Fatalf("readConfig: %v", err)
	}
	if len(got.Extensions) != 2 || got.Extensions[0] != "Cite" || got.Extensions[1] != "VisualEditor" {
		t.Errorf("extensions = %v, want [Cite VisualEditor]", got.Extensions)
	}
	if len(got.Skins) != 1 || got.Skins[0] != "Vector" {
		t.Errorf("skins = %v, want [Vector]", got.Skins)
	}
}

func TestWriteConfigHeader(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "main.yaml")

	cfg := configYAML{Extensions: []string{"Cite"}}
	if err := writeConfig(path, cfg); err != nil {
		t.Fatalf("writeConfig: %v", err)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if !strings.HasPrefix(string(data), "# This file is managed by Canasta") {
		t.Errorf("expected header comment, got:\n%s", data)
	}
}

func TestWriteConfigDeletesEmptyFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "main.yaml")

	// Write a non-empty config first
	cfg := configYAML{Extensions: []string{"Cite"}}
	if err := writeConfig(path, cfg); err != nil {
		t.Fatalf("writeConfig: %v", err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("file should exist after write: %v", err)
	}

	// Write empty config — should delete the file
	if err := writeConfig(path, configYAML{}); err != nil {
		t.Fatalf("writeConfig empty: %v", err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Error("file should be deleted after writing empty config")
	}
}

func newTestInstance(t *testing.T) config.Installation {
	t.Helper()
	dir := t.TempDir()
	return config.Installation{Path: dir}
}

func TestEnableExtension(t *testing.T) {
	inst := newTestInstance(t)
	if err := Enable("VisualEditor", "", inst, extConstants); err != nil {
		t.Fatalf("Enable: %v", err)
	}

	path := configPath(inst.Path, "")
	cfg, err := readConfig(path)
	if err != nil {
		t.Fatalf("readConfig: %v", err)
	}
	if !slices.Contains(cfg.Extensions, "VisualEditor") {
		t.Errorf("expected VisualEditor in extensions, got %v", cfg.Extensions)
	}
	if len(cfg.Skins) != 0 {
		t.Errorf("expected no skins, got %v", cfg.Skins)
	}
}

func TestEnableAlreadyEnabled(t *testing.T) {
	inst := newTestInstance(t)
	if err := Enable("Cite", "", inst, extConstants); err != nil {
		t.Fatalf("Enable first: %v", err)
	}
	if err := Enable("Cite", "", inst, extConstants); err != nil {
		t.Fatalf("Enable second: %v", err)
	}

	cfg, _ := readConfig(configPath(inst.Path, ""))
	count := 0
	for _, e := range cfg.Extensions {
		if e == "Cite" {
			count++
		}
	}
	if count != 1 {
		t.Errorf("expected Cite once, found %d times", count)
	}
}

func TestEnablePerWiki(t *testing.T) {
	inst := newTestInstance(t)
	if err := Enable("Cite", "docs", inst, extConstants); err != nil {
		t.Fatalf("Enable per-wiki: %v", err)
	}

	path := configPath(inst.Path, "docs")
	cfg, err := readConfig(path)
	if err != nil {
		t.Fatalf("readConfig: %v", err)
	}
	if !slices.Contains(cfg.Extensions, "Cite") {
		t.Errorf("expected Cite in per-wiki extensions, got %v", cfg.Extensions)
	}

	// Global config should not exist
	globalPath := configPath(inst.Path, "")
	if _, err := os.Stat(globalPath); !os.IsNotExist(err) {
		t.Error("global config should not exist for per-wiki enable")
	}
}

func TestEnableSkin(t *testing.T) {
	inst := newTestInstance(t)
	if err := Enable("Vector", "", inst, skinConstants); err != nil {
		t.Fatalf("Enable skin: %v", err)
	}

	cfg, _ := readConfig(configPath(inst.Path, ""))
	if !slices.Contains(cfg.Skins, "Vector") {
		t.Errorf("expected Vector in skins, got %v", cfg.Skins)
	}
	if len(cfg.Extensions) != 0 {
		t.Errorf("expected no extensions, got %v", cfg.Extensions)
	}
}

func TestMixedExtensionsAndSkins(t *testing.T) {
	inst := newTestInstance(t)
	if err := Enable("VisualEditor", "", inst, extConstants); err != nil {
		t.Fatalf("Enable ext: %v", err)
	}
	if err := Enable("Vector", "", inst, skinConstants); err != nil {
		t.Fatalf("Enable skin: %v", err)
	}

	cfg, _ := readConfig(configPath(inst.Path, ""))
	if !slices.Contains(cfg.Extensions, "VisualEditor") {
		t.Errorf("expected VisualEditor in extensions, got %v", cfg.Extensions)
	}
	if !slices.Contains(cfg.Skins, "Vector") {
		t.Errorf("expected Vector in skins, got %v", cfg.Skins)
	}
}

func TestDisableExtension(t *testing.T) {
	inst := newTestInstance(t)
	_ = Enable("VisualEditor", "", inst, extConstants)
	_ = Enable("Cite", "", inst, extConstants)

	if err := Disable("VisualEditor", "", inst, extConstants); err != nil {
		t.Fatalf("Disable: %v", err)
	}

	cfg, _ := readConfig(configPath(inst.Path, ""))
	if slices.Contains(cfg.Extensions, "VisualEditor") {
		t.Error("VisualEditor should be removed")
	}
	if !slices.Contains(cfg.Extensions, "Cite") {
		t.Error("Cite should still be present")
	}
}

func TestDisableNotEnabled(t *testing.T) {
	inst := newTestInstance(t)
	err := Disable("VisualEditor", "", inst, extConstants)
	if err == nil {
		t.Error("expected error when disabling non-enabled extension")
	}
}

func TestDisableRemovesFileWhenEmpty(t *testing.T) {
	inst := newTestInstance(t)
	_ = Enable("Cite", "", inst, extConstants)

	if err := Disable("Cite", "", inst, extConstants); err != nil {
		t.Fatalf("Disable: %v", err)
	}

	path := configPath(inst.Path, "")
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Error("main.yaml should be deleted when last entry is removed")
	}
}

func TestCheckEnabled(t *testing.T) {
	inst := newTestInstance(t)
	_ = Enable("Cite", "", inst, extConstants)

	name, err := CheckEnabled("Cite", "", inst, extConstants)
	if err != nil {
		t.Fatalf("CheckEnabled: %v", err)
	}
	if name != "Cite" {
		t.Errorf("expected 'Cite', got %q", name)
	}

	_, err = CheckEnabled("Missing", "", inst, extConstants)
	if err == nil {
		t.Error("expected error for non-enabled extension")
	}
}
