package maintenance

import (
	"fmt"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// extMockOrchestrator records calls and returns configurable results.
type extMockOrchestrator struct {
	calls          []string
	execOutputs    map[string]string // command -> output
	execErr        error
	streamingCalls []string
	streamingErr   error
}

func (m *extMockOrchestrator) CheckDependencies() error { return nil }
func (m *extMockOrchestrator) GetRepoLink() string      { return "" }
func (m *extMockOrchestrator) Start(inst config.Installation) error {
	return nil
}
func (m *extMockOrchestrator) Stop(inst config.Installation) error {
	return nil
}
func (m *extMockOrchestrator) Update(installPath string) (*orchestrators.UpdateReport, error) {
	return nil, nil
}
func (m *extMockOrchestrator) Destroy(installPath string) (string, error) {
	return "", nil
}
func (m *extMockOrchestrator) ExecWithError(installPath, service, command string) (string, error) {
	m.calls = append(m.calls, command)
	if m.execOutputs != nil {
		for key, output := range m.execOutputs {
			if strings.Contains(command, key) {
				return output, m.execErr
			}
		}
	}
	return "", m.execErr
}
func (m *extMockOrchestrator) ExecStreaming(installPath, service, command string) error {
	m.streamingCalls = append(m.streamingCalls, command)
	return m.streamingErr
}
func (m *extMockOrchestrator) CheckRunningStatus(inst config.Installation) error {
	return nil
}
func (m *extMockOrchestrator) CopyFrom(installPath, service, containerPath, hostPath string) error {
	return nil
}
func (m *extMockOrchestrator) CopyTo(installPath, service, hostPath, containerPath string) error {
	return nil
}

func (m *extMockOrchestrator) RunBackup(installPath, envPath string, volumes map[string]string, args ...string) (string, error) {
	return "", nil
}

func (m *extMockOrchestrator) RestoreFromBackupVolume(installPath string, dirs map[string]string) error {
	return nil
}
func (m *extMockOrchestrator) InitConfig(installPath string) error {
	return nil
}
func (m *extMockOrchestrator) UpdateConfig(installPath string) error {
	return nil
}
func (m *extMockOrchestrator) MigrateConfig(installPath string, dryRun bool) (bool, error) {
	return false, nil
}
func (m *extMockOrchestrator) StopAndStart(inst config.Installation) error {
	return nil
}

// --- Parse helpers ---

func TestParseExtensionNames(t *testing.T) {
	output := `extensions/CirrusSearch/maintenance
canasta-extensions/SemanticMediaWiki/maintenance
extensions/Cargo/maintenance
canasta-extensions/CirrusSearch/maintenance`

	names := parseExtensionNames(output)
	expected := []string{"Cargo", "CirrusSearch", "SemanticMediaWiki"}

	if len(names) != len(expected) {
		t.Fatalf("expected %d names, got %d: %v", len(expected), len(names), names)
	}
	for i, name := range names {
		if name != expected[i] {
			t.Errorf("name[%d] = %q, want %q", i, name, expected[i])
		}
	}
}

func TestParseExtensionNamesEmpty(t *testing.T) {
	names := parseExtensionNames("")
	if len(names) != 0 {
		t.Errorf("expected empty, got %v", names)
	}
}

func TestParseScriptNames(t *testing.T) {
	output := `extensions/SemanticMediaWiki/maintenance/rebuildData.php
extensions/SemanticMediaWiki/maintenance/setupStore.php
canasta-extensions/SemanticMediaWiki/maintenance/rebuildData.php`

	scripts := parseScriptNames(output)
	expected := []string{"rebuildData.php", "setupStore.php"}

	if len(scripts) != len(expected) {
		t.Fatalf("expected %d scripts, got %d: %v", len(expected), len(scripts), scripts)
	}
	for i, s := range scripts {
		if s != expected[i] {
			t.Errorf("script[%d] = %q, want %q", i, s, expected[i])
		}
	}
}

func TestParseLoadedExtensions(t *testing.T) {
	output := "CirrusSearch\nSemanticMediaWiki\nVisualEditor\n"
	names := parseLoadedExtensions(output)
	expected := []string{"CirrusSearch", "SemanticMediaWiki", "VisualEditor"}
	if len(names) != len(expected) {
		t.Fatalf("expected %d names, got %d: %v", len(expected), len(names), names)
	}
	for i, name := range names {
		if name != expected[i] {
			t.Errorf("name[%d] = %q, want %q", i, name, expected[i])
		}
	}
}

func TestParseLoadedExtensionsEmpty(t *testing.T) {
	names := parseLoadedExtensions("")
	if len(names) != 0 {
		t.Errorf("expected empty, got %v", names)
	}
}

// --- List extensions ---

func TestListExtensionsWithMaintenance(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php":        "CirrusSearch\nSemanticMediaWiki\nCargo\n",
			"find extensions": "extensions/CirrusSearch/maintenance\ncanasta-extensions/SemanticMediaWiki/maintenance\nextensions/Cargo/maintenance\n",
		},
	}
	inst := config.Installation{Path: "/test"}
	// Pass a specific wiki to avoid needing wikis.yaml
	err := listExtensionsWithMaintenanceWith(mock, inst, "main", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestListExtensionsFiltersToLoaded(t *testing.T) {
	// Cargo has maintenance dir but is NOT loaded
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php":        "CirrusSearch\nSemanticMediaWiki\n",
			"find extensions": "extensions/CirrusSearch/maintenance\ncanasta-extensions/SemanticMediaWiki/maintenance\nextensions/Cargo/maintenance\n",
		},
	}
	inst := config.Installation{Path: "/test"}
	// We can't easily capture stdout in this test, but at least verify no error
	err := listExtensionsWithMaintenanceWith(mock, inst, "main", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Verify eval.php was called
	foundEval := false
	for _, cmd := range mock.calls {
		if strings.Contains(cmd, "eval.php") {
			foundEval = true
			break
		}
	}
	if !foundEval {
		t.Error("expected eval.php to be called to query loaded extensions")
	}
}

// --- List extension scripts ---

func TestListExtensionScripts(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php": "SemanticMediaWiki\n",
			"test -d":  "exists",
			"find extensions": `extensions/SemanticMediaWiki/maintenance/rebuildData.php
extensions/SemanticMediaWiki/maintenance/setupStore.php`,
		},
	}
	inst := config.Installation{Path: "/test"}
	err := listExtensionScriptsWith(mock, inst, "SemanticMediaWiki", "main", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestListExtensionScriptsNotLoaded(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php": "VisualEditor\nCite\n",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := listExtensionScriptsWith(mock, inst, "SemanticMediaWiki", "main", false)
	if err == nil {
		t.Fatal("expected error for extension not loaded")
	}
	if !strings.Contains(err.Error(), "not loaded") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestListExtensionScriptsNotFound(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php": "NonExistent\n",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := listExtensionScriptsWith(mock, inst, "NonExistent", "main", false)
	if err == nil {
		t.Fatal("expected error for non-existent extension")
	}
	if !strings.Contains(err.Error(), "no maintenance directory") {
		t.Errorf("unexpected error: %v", err)
	}
}

// --- Run extension scripts ---

func TestRunExtensionScript(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php":                              "SemanticMediaWiki\n",
			"test -d extensions/SemanticMediaWiki": "exists",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "SemanticMediaWiki", "rebuildData.php", "main")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(mock.streamingCalls) != 1 {
		t.Fatalf("expected 1 streaming call, got %d", len(mock.streamingCalls))
	}
	cmd := mock.streamingCalls[0]
	if !strings.Contains(cmd, "extensions/SemanticMediaWiki/maintenance/rebuildData.php") {
		t.Errorf("expected path to rebuildData.php, got: %s", cmd)
	}
}

func TestRunExtensionScriptWithWiki(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php":                          "CirrusSearch\n",
			"test -d extensions/CirrusSearch": "exists",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "CirrusSearch", "UpdateSearchIndexConfig.php", "docs")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	cmd := mock.streamingCalls[0]
	if !strings.Contains(cmd, "--wiki=docs") {
		t.Errorf("expected --wiki=docs, got: %s", cmd)
	}
}

func TestRunExtensionScriptWithArgs(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php":                              "SemanticMediaWiki\n",
			"test -d extensions/SemanticMediaWiki": "exists",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "SemanticMediaWiki", "rebuildData.php -s 1000 -e 2000", "main")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	cmd := mock.streamingCalls[0]
	if !strings.Contains(cmd, "-s 1000 -e 2000") {
		t.Errorf("expected script args, got: %s", cmd)
	}
}

func TestRunExtensionScriptNotLoaded(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php": "VisualEditor\nCite\n",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "SemanticMediaWiki", "rebuildData.php", "main")
	if err == nil {
		t.Fatal("expected error for extension not loaded")
	}
	if !strings.Contains(err.Error(), "not loaded") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestRunExtensionScriptNotFound(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php": "NonExistent\n",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "NonExistent", "something.php", "main")
	if err == nil {
		t.Fatal("expected error for non-existent extension")
	}
	if !strings.Contains(err.Error(), "no maintenance directory") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestRunExtensionScriptStreamingError(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php":                              "SemanticMediaWiki\n",
			"test -d extensions/SemanticMediaWiki": "exists",
		},
		streamingErr: fmt.Errorf("container error"),
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "SemanticMediaWiki", "rebuildData.php", "main")
	if err == nil {
		t.Fatal("expected error when streaming fails")
	}
	if !strings.Contains(err.Error(), "failed") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestRunExtensionScriptCanastaExtensions(t *testing.T) {
	// Extension found in canasta-extensions, not extensions
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php":                          "Foo\n",
			"test -d canasta-extensions/Foo": "exists",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "Foo", "doSomething.php", "main")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	cmd := mock.streamingCalls[0]
	if !strings.Contains(cmd, "canasta-extensions/Foo/maintenance/doSomething.php") {
		t.Errorf("expected canasta-extensions path, got: %s", cmd)
	}
}

// --- resolveWikiFlag ---

func TestResolveWikiFlagNoWiki(t *testing.T) {
	wiki, script, err := resolveWikiFlag("", "rebuildData.php -s 1000")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if wiki != "" {
		t.Errorf("expected empty wiki, got %q", wiki)
	}
	if script != "rebuildData.php -s 1000" {
		t.Errorf("expected unchanged script, got %q", script)
	}
}

func TestResolveWikiFlagCLIOnly(t *testing.T) {
	wiki, script, err := resolveWikiFlag("docs", "rebuildData.php")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if wiki != "docs" {
		t.Errorf("expected wiki=docs, got %q", wiki)
	}
	if script != "rebuildData.php" {
		t.Errorf("expected unchanged script, got %q", script)
	}
}

func TestResolveWikiFlagScriptOnly(t *testing.T) {
	wiki, script, err := resolveWikiFlag("", "rebuildData.php --wiki=main")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if wiki != "main" {
		t.Errorf("expected wiki=main, got %q", wiki)
	}
	if strings.Contains(script, "--wiki") {
		t.Errorf("expected --wiki removed from script, got %q", script)
	}
}

func TestResolveWikiFlagBothSame(t *testing.T) {
	wiki, script, err := resolveWikiFlag("docs", "rebuildData.php --wiki=docs")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if wiki != "docs" {
		t.Errorf("expected wiki=docs, got %q", wiki)
	}
	if strings.Contains(script, "--wiki") {
		t.Errorf("expected --wiki removed from script, got %q", script)
	}
}

func TestResolveWikiFlagConflict(t *testing.T) {
	_, _, err := resolveWikiFlag("docs", "rebuildData.php --wiki=main")
	if err == nil {
		t.Fatal("expected error for conflicting --wiki values")
	}
	if !strings.Contains(err.Error(), "conflicting") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestResolveWikiFlagScriptWithArgsAndWiki(t *testing.T) {
	wiki, script, err := resolveWikiFlag("", "rebuildData.php -s 1000 --wiki=main -e 2000")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if wiki != "main" {
		t.Errorf("expected wiki=main, got %q", wiki)
	}
	if strings.Contains(script, "--wiki") {
		t.Errorf("expected --wiki removed from script, got %q", script)
	}
	if !strings.Contains(script, "-s 1000") || !strings.Contains(script, "-e 2000") {
		t.Errorf("expected other args preserved, got %q", script)
	}
}

func TestRunExtensionScriptWikiInScriptString(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"eval.php":                              "SemanticMediaWiki\n",
			"test -d extensions/SemanticMediaWiki": "exists",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "SemanticMediaWiki", "rebuildData.php --wiki=docs", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	cmd := mock.streamingCalls[0]
	if !strings.Contains(cmd, "--wiki=docs") {
		t.Errorf("expected --wiki=docs, got: %s", cmd)
	}
	// Should not have --wiki twice
	if strings.Count(cmd, "--wiki") != 1 {
		t.Errorf("expected exactly one --wiki, got: %s", cmd)
	}
}

func TestRunExtensionScriptWikiConflict(t *testing.T) {
	mock := &extMockOrchestrator{
		execOutputs: map[string]string{
			"test -d extensions/SemanticMediaWiki": "exists",
		},
	}
	inst := config.Installation{Path: "/test"}
	err := runExtensionScriptWith(mock, inst, "SemanticMediaWiki", "rebuildData.php --wiki=main", "docs")
	if err == nil {
		t.Fatal("expected error for conflicting --wiki values")
	}
	if !strings.Contains(err.Error(), "conflicting") {
		t.Errorf("unexpected error: %v", err)
	}
}
