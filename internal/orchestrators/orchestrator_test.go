package orchestrators

import (
	"fmt"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
)

// mockOrchestrator records calls for testing
type mockOrchestrator struct {
	calls       []string
	execOutput  string
	execErr     error
	copyToErr   error
	startErr    error
	stopErr     error
}

func (m *mockOrchestrator) CheckDependencies() error { return nil }
func (m *mockOrchestrator) GetRepoLink() string      { return "https://example.com/repo.git" }

func (m *mockOrchestrator) Start(instance config.Installation) error {
	m.calls = append(m.calls, "Start")
	return m.startErr
}

func (m *mockOrchestrator) Stop(instance config.Installation) error {
	m.calls = append(m.calls, "Stop")
	return m.stopErr
}

func (m *mockOrchestrator) Update(installPath string) (*UpdateReport, error) {
	return nil, nil
}

func (m *mockOrchestrator) Destroy(installPath string) (string, error) {
	return "", nil
}

func (m *mockOrchestrator) ExecWithError(installPath, service, command string) (string, error) {
	m.calls = append(m.calls, fmt.Sprintf("ExecWithError:%s:%s", service, command))
	return m.execOutput, m.execErr
}

func (m *mockOrchestrator) ExecStreaming(installPath, service, command string) error {
	return nil
}

func (m *mockOrchestrator) CheckRunningStatus(instance config.Installation) error {
	return nil
}

func (m *mockOrchestrator) CopyFrom(installPath, service, containerPath, hostPath string) error {
	m.calls = append(m.calls, fmt.Sprintf("CopyFrom:%s:%s:%s", service, containerPath, hostPath))
	return nil
}

func (m *mockOrchestrator) CopyTo(installPath, service, hostPath, containerPath string) error {
	m.calls = append(m.calls, fmt.Sprintf("CopyTo:%s:%s:%s", service, hostPath, containerPath))
	return m.copyToErr
}

func (m *mockOrchestrator) RunRestic(installPath, envPath string, volumes map[string]string, args ...string) (string, error) {
	return "", nil
}

func TestNew(t *testing.T) {
	tests := []struct {
		name    string
		id      string
		wantErr bool
	}{
		{"compose", "compose", false},
		{"docker-compose alias", "docker-compose", false},
		{"kubernetes not yet implemented", "kubernetes", true},
		{"k8s not yet implemented", "k8s", true},
		{"unknown", "unknown-orch", true},
		{"empty", "", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			orch, err := New(tt.id)
			if (err != nil) != tt.wantErr {
				t.Errorf("New(%q) error = %v, wantErr %v", tt.id, err, tt.wantErr)
			}
			if !tt.wantErr && orch == nil {
				t.Error("expected non-nil orchestrator")
			}
		})
	}
}

func TestNewKubernetesNotYetImplemented(t *testing.T) {
	for _, id := range []string{"kubernetes", "k8s"} {
		_, err := New(id)
		if err == nil {
			t.Fatalf("New(%q) expected error, got nil", id)
		}
		if !strings.Contains(err.Error(), "not yet implemented") {
			t.Errorf("New(%q) error = %q, want 'not yet implemented'", id, err.Error())
		}
	}
}

func TestExec(t *testing.T) {
	mock := &mockOrchestrator{execOutput: "success output"}
	output, err := Exec(mock, "/tmp/test", "web", "php test.php")
	if err != nil {
		t.Fatalf("Exec() error = %v", err)
	}
	if output != "success output" {
		t.Errorf("Exec() output = %q, want %q", output, "success output")
	}
}

func TestExecError(t *testing.T) {
	mock := &mockOrchestrator{
		execOutput: "error details",
		execErr:    fmt.Errorf("command failed"),
	}
	_, err := Exec(mock, "/tmp/test", "web", "php test.php")
	if err == nil {
		t.Fatal("expected error from Exec")
	}
	if !strings.Contains(err.Error(), "error details") {
		t.Errorf("error should contain output, got: %v", err)
	}
}

func TestStopAndStart(t *testing.T) {
	mock := &mockOrchestrator{}
	instance := config.Installation{Id: "test", Path: "/tmp/test"}

	err := StopAndStart(mock, instance)
	if err != nil {
		t.Fatalf("StopAndStart() error = %v", err)
	}

	if len(mock.calls) != 2 || mock.calls[0] != "Stop" || mock.calls[1] != "Start" {
		t.Errorf("expected [Stop, Start], got %v", mock.calls)
	}
}

func TestStopAndStartStopError(t *testing.T) {
	mock := &mockOrchestrator{stopErr: fmt.Errorf("stop failed")}
	instance := config.Installation{Id: "test", Path: "/tmp/test"}

	err := StopAndStart(mock, instance)
	if err == nil {
		t.Fatal("expected error when Stop fails")
	}

	// Start should not be called
	for _, call := range mock.calls {
		if call == "Start" {
			t.Error("Start should not be called when Stop fails")
		}
	}
}

func TestImportDatabase(t *testing.T) {
	mock := &mockOrchestrator{}
	instance := config.Installation{Id: "test", Path: "/tmp/test"}

	err := ImportDatabase(mock, "mywiki", "/path/to/dump.sql", "secret", instance)
	if err != nil {
		t.Fatalf("ImportDatabase() error = %v", err)
	}

	// Verify CopyTo was called
	hasCopyTo := false
	hasCreateDB := false
	hasImport := false
	for _, call := range mock.calls {
		if strings.HasPrefix(call, "CopyTo:") {
			hasCopyTo = true
		}
		if strings.Contains(call, "CREATE DATABASE") {
			hasCreateDB = true
		}
		if strings.Contains(call, "< /tmp/mywiki.sql") {
			hasImport = true
		}
	}

	if !hasCopyTo {
		t.Error("expected CopyTo call")
	}
	if !hasCreateDB {
		t.Error("expected CREATE DATABASE call")
	}
	if !hasImport {
		t.Error("expected import (mysql <) call")
	}
}

func TestImportDatabaseCompressed(t *testing.T) {
	mock := &mockOrchestrator{}
	instance := config.Installation{Id: "test", Path: "/tmp/test"}

	err := ImportDatabase(mock, "mywiki", "/path/to/dump.sql.gz", "secret", instance)
	if err != nil {
		t.Fatalf("ImportDatabase() error = %v", err)
	}

	// Verify gunzip was called
	hasGunzip := false
	for _, call := range mock.calls {
		if strings.Contains(call, "gunzip") {
			hasGunzip = true
		}
	}
	if !hasGunzip {
		t.Error("expected gunzip call for .sql.gz file")
	}
}

func TestImportDatabaseCopyError(t *testing.T) {
	mock := &mockOrchestrator{copyToErr: fmt.Errorf("copy failed")}
	instance := config.Installation{Id: "test", Path: "/tmp/test"}

	err := ImportDatabase(mock, "mywiki", "/path/to/dump.sql", "secret", instance)
	if err == nil {
		t.Fatal("expected error when CopyTo fails")
	}
}

func TestImportDatabaseDefaultPassword(t *testing.T) {
	mock := &mockOrchestrator{}
	instance := config.Installation{Id: "test", Path: "/tmp/test"}

	err := ImportDatabase(mock, "mywiki", "/path/to/dump.sql", "", instance)
	if err != nil {
		t.Fatalf("ImportDatabase() error = %v", err)
	}

	// Verify default password "mediawiki" is used
	hasDefaultPw := false
	for _, call := range mock.calls {
		if strings.Contains(call, "mediawiki") {
			hasDefaultPw = true
		}
	}
	if !hasDefaultPw {
		t.Error("expected default password 'mediawiki' to be used")
	}
}
