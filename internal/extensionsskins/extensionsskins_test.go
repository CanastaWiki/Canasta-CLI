package extensionsskins

import (
	"os"
	"path/filepath"
	"slices"
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

func TestArticle(t *testing.T) {
	gotests := []struct {
		input string
		want  string
	}{
		{"extension", "an"},
		{"skin", "a"},
		{"apple", "an"},
		{"Extension", "an"},
		{"item", "an"},
		{"Object", "an"},
		{"umbrella", "an"},
		{"Umbrella", "an"},
		{"wiki", "a"},
		{"", "a"},
	}

	for _, tt := range gotests {
		got := article(tt.input)
		if got != tt.want {
			t.Errorf("article(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

// --- YAML config helpers ---

var extConstants = Item{
	Name:                     "Canasta extension",
	CmdName:                  "extension",
	Plural:                   "extensions",
	RelativeInstancePath: "extensions",
	PhpCommand:               "wfLoadExtension",
	ExampleNames:             "VisualEditor,Cite,ParserFunctions",
}

var skinConstants = Item{
	Name:                     "Canasta skin",
	CmdName:                  "skin",
	Plural:                   "skins",
	RelativeInstancePath: "skins",
	PhpCommand:               "wfLoadSkin",
	ExampleNames:             "Timeless",
}

func TestConfigPath(t *testing.T) {
	base := filepath.Join("/srv", "canasta", "myinstance")

	got := configPath(base, "")
	want := filepath.Join(base, "config", "settings", "global", "settings.yaml")
	if got != want {
		t.Errorf("configPath global = %q, want %q", got, want)
	}

	got = configPath(base, "docs")
	want = filepath.Join(base, "config", "settings", "wikis", "docs", "settings.yaml")
	if got != want {
		t.Errorf("configPath per-wiki = %q, want %q", got, want)
	}
}

func TestReadConfigEmpty(t *testing.T) {
	cfg, err := readConfig("/nonexistent/path/settings.yaml")
	if err != nil {
		t.Fatalf("readConfig on missing file: %v", err)
	}
	if len(cfg.Extensions) != 0 || len(cfg.Skins) != 0 {
		t.Errorf("expected empty config, got %+v", cfg)
	}
}

func TestWriteAndReadConfig(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "settings.yaml")

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

func TestWriteConfigDeletesEmptyFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "settings.yaml")

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

func newTestInstance(t *testing.T) config.Instance {
	t.Helper()
	dir := t.TempDir()
	return config.Instance{Path: dir}
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
		t.Error("settings.yaml should be deleted when last entry is removed")
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

func TestDisableSkin(t *testing.T) {
	inst := newTestInstance(t)
	_ = Enable("Vector", "", inst, skinConstants)
	_ = Enable("Timeless", "", inst, skinConstants)

	if err := Disable("Vector", "", inst, skinConstants); err != nil {
		t.Fatalf("Disable skin: %v", err)
	}

	cfg, err := readConfig(configPath(inst.Path, ""))
	if err != nil {
		t.Fatalf("readConfig: %v", err)
	}
	if slices.Contains(cfg.Skins, "Vector") {
		t.Error("Vector should be removed from skins")
	}
	if !slices.Contains(cfg.Skins, "Timeless") {
		t.Error("Timeless should still be present in skins")
	}
	if len(cfg.Extensions) != 0 {
		t.Errorf("extensions should be unaffected, got %v", cfg.Extensions)
	}
}

func TestDisablePerWikiExtension(t *testing.T) {
	inst := newTestInstance(t)
	_ = Enable("Cite", "docs", inst, extConstants)
	_ = Enable("VisualEditor", "docs", inst, extConstants)

	if err := Disable("Cite", "docs", inst, extConstants); err != nil {
		t.Fatalf("Disable per-wiki extension: %v", err)
	}

	// Per-wiki config should have Cite removed but VisualEditor intact.
	wikiCfg, err := readConfig(configPath(inst.Path, "docs"))
	if err != nil {
		t.Fatalf("readConfig: %v", err)
	}
	if slices.Contains(wikiCfg.Extensions, "Cite") {
		t.Error("Cite should be removed from per-wiki config")
	}
	if !slices.Contains(wikiCfg.Extensions, "VisualEditor") {
		t.Error("VisualEditor should still be present in per-wiki config")
	}

	// Global config must not have been created.
	globalPath := configPath(inst.Path, "")
	if _, err := os.Stat(globalPath); !os.IsNotExist(err) {
		t.Error("global config should not exist after per-wiki disable")
	}
}

func TestCheckEnabledSkin(t *testing.T) {
	inst := newTestInstance(t)
	_ = Enable("Vector", "", inst, skinConstants)

	name, err := CheckEnabled("Vector", "", inst, skinConstants)
	if err != nil {
		t.Fatalf("CheckEnabled skin: %v", err)
	}
	if name != "Vector" {
		t.Errorf("expected 'Vector', got %q", name)
	}

	_, err = CheckEnabled("Timeless", "", inst, skinConstants)
	if err == nil {
		t.Error("expected error for non-enabled skin")
	}
}
