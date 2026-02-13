package maintenance

import (
	"fmt"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// smwMockOrchestrator records calls and returns configurable results.
type smwMockOrchestrator struct {
	calls          []string
	execOutput     string
	execErr        error
	streamingErr   error
	streamingCalls []string
}

func (m *smwMockOrchestrator) CheckDependencies() error { return nil }
func (m *smwMockOrchestrator) GetRepoLink() string      { return "" }
func (m *smwMockOrchestrator) Start(inst config.Installation) error {
	return nil
}
func (m *smwMockOrchestrator) Stop(inst config.Installation) error {
	return nil
}
func (m *smwMockOrchestrator) Update(installPath string) (*orchestrators.UpdateReport, error) {
	return nil, nil
}
func (m *smwMockOrchestrator) Destroy(installPath string) (string, error) {
	return "", nil
}
func (m *smwMockOrchestrator) ExecWithError(installPath, service, command string) (string, error) {
	m.calls = append(m.calls, fmt.Sprintf("ExecWithError:%s:%s", service, command))
	return m.execOutput, m.execErr
}
func (m *smwMockOrchestrator) ExecStreaming(installPath, service, command string) error {
	m.streamingCalls = append(m.streamingCalls, command)
	return m.streamingErr
}
func (m *smwMockOrchestrator) CheckRunningStatus(inst config.Installation) error {
	return nil
}
func (m *smwMockOrchestrator) CopyFrom(installPath, service, containerPath, hostPath string) error {
	return nil
}
func (m *smwMockOrchestrator) CopyTo(installPath, service, hostPath, containerPath string) error {
	return nil
}
func (m *smwMockOrchestrator) StopAndStart(inst config.Installation) error {
	return nil
}

func TestSMWRebuildNotInstalled(t *testing.T) {
	mock := &smwMockOrchestrator{execOutput: ""}
	inst := config.Installation{Path: "/test"}
	err := runSMWRebuildWith(mock, inst, "mywiki", "")
	if err == nil {
		t.Fatal("expected error when SMW is not installed")
	}
	if !strings.Contains(err.Error(), "not installed") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestSMWRebuildBasic(t *testing.T) {
	mock := &smwMockOrchestrator{execOutput: "exists"}
	inst := config.Installation{Path: "/test"}
	err := runSMWRebuildWith(mock, inst, "mywiki", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(mock.streamingCalls) != 1 {
		t.Fatalf("expected 1 streaming call, got %d", len(mock.streamingCalls))
	}
	cmd := mock.streamingCalls[0]
	if !strings.Contains(cmd, "rebuildData.php") {
		t.Errorf("expected rebuildData.php in command, got: %s", cmd)
	}
	if !strings.Contains(cmd, "--wiki=mywiki") {
		t.Errorf("expected --wiki=mywiki in command, got: %s", cmd)
	}
}

func TestSMWRebuildNoWiki(t *testing.T) {
	mock := &smwMockOrchestrator{execOutput: "exists"}
	inst := config.Installation{Path: "/test"}
	err := runSMWRebuildWith(mock, inst, "", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	cmd := mock.streamingCalls[0]
	if strings.Contains(cmd, "--wiki") {
		t.Errorf("expected no --wiki flag, got: %s", cmd)
	}
}

func TestSMWRebuildExtraArgs(t *testing.T) {
	mock := &smwMockOrchestrator{execOutput: "exists"}
	inst := config.Installation{Path: "/test"}
	err := runSMWRebuildWith(mock, inst, "mywiki", "--startidfile /tmp/progress")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	cmd := mock.streamingCalls[0]
	if !strings.Contains(cmd, "--startidfile /tmp/progress") {
		t.Errorf("expected extra args in command, got: %s", cmd)
	}
}

func TestSMWRebuildStreamingError(t *testing.T) {
	mock := &smwMockOrchestrator{
		execOutput:   "exists",
		streamingErr: fmt.Errorf("container error"),
	}
	inst := config.Installation{Path: "/test"}
	err := runSMWRebuildWith(mock, inst, "mywiki", "")
	if err == nil {
		t.Fatal("expected error when streaming fails")
	}
	if !strings.Contains(err.Error(), "rebuildData.php failed") {
		t.Errorf("unexpected error: %v", err)
	}
}
