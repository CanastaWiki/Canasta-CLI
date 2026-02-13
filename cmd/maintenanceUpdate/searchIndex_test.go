package maintenance

import (
	"fmt"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// mockOrch implements orchestrators.Orchestrator for testing.
type mockOrch struct {
	execCalls      []string
	streamingCalls []string
	execOutput     string
	execErr        error
	streamingErr   error
}

func (m *mockOrch) CheckDependencies() error                                       { return nil }
func (m *mockOrch) GetRepoLink() string                                            { return "" }
func (m *mockOrch) Start(_ config.Installation) error                              { return nil }
func (m *mockOrch) Stop(_ config.Installation) error                               { return nil }
func (m *mockOrch) Update(_ string) (*orchestrators.UpdateReport, error)           { return nil, nil }
func (m *mockOrch) Destroy(_ string) (string, error)                               { return "", nil }
func (m *mockOrch) CheckRunningStatus(_ config.Installation) error                 { return nil }
func (m *mockOrch) CopyFrom(_, _, _, _ string) error                               { return nil }
func (m *mockOrch) CopyTo(_, _, _, _ string) error                                 { return nil }

func (m *mockOrch) ExecWithError(_, _, command string) (string, error) {
	m.execCalls = append(m.execCalls, command)
	return m.execOutput, m.execErr
}

func (m *mockOrch) ExecStreaming(_, _, command string) error {
	m.streamingCalls = append(m.streamingCalls, command)
	return m.streamingErr
}

func TestRunSearchIndex_CirrusSearchNotInstalled(t *testing.T) {
	mock := &mockOrch{execOutput: ""}
	err := runSearchIndexWith(mock, "/tmp/test", "mywiki", false)
	if err == nil {
		t.Fatal("expected error when CirrusSearch is not installed")
	}
	if !strings.Contains(err.Error(), "CirrusSearch extension is not installed") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestRunSearchIndex_DefaultFlags(t *testing.T) {
	mock := &mockOrch{execOutput: "exists"}
	err := runSearchIndexWith(mock, "/tmp/test", "mywiki", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(mock.streamingCalls) != 3 {
		t.Fatalf("expected 3 streaming calls, got %d", len(mock.streamingCalls))
	}

	// Step 1: UpdateSearchIndexConfig with safe flags
	if !strings.Contains(mock.streamingCalls[0], "UpdateSearchIndexConfig.php") {
		t.Errorf("step 1 should call UpdateSearchIndexConfig.php, got: %s", mock.streamingCalls[0])
	}
	if !strings.Contains(mock.streamingCalls[0], "--reindexAndRemoveOk --indexIdentifier now") {
		t.Errorf("step 1 should use safe flags, got: %s", mock.streamingCalls[0])
	}
	if !strings.Contains(mock.streamingCalls[0], "--wiki=mywiki") {
		t.Errorf("step 1 should include --wiki flag, got: %s", mock.streamingCalls[0])
	}

	// Step 2: ForceSearchIndex --skipLinks --indexOnSkip
	if !strings.Contains(mock.streamingCalls[1], "ForceSearchIndex.php --skipLinks --indexOnSkip") {
		t.Errorf("step 2 should call ForceSearchIndex.php --skipLinks --indexOnSkip, got: %s", mock.streamingCalls[1])
	}
	if !strings.Contains(mock.streamingCalls[1], "--wiki=mywiki") {
		t.Errorf("step 2 should include --wiki flag, got: %s", mock.streamingCalls[1])
	}

	// Step 3: ForceSearchIndex --skipParse
	if !strings.Contains(mock.streamingCalls[2], "ForceSearchIndex.php --skipParse") {
		t.Errorf("step 3 should call ForceSearchIndex.php --skipParse, got: %s", mock.streamingCalls[2])
	}
	if !strings.Contains(mock.streamingCalls[2], "--wiki=mywiki") {
		t.Errorf("step 3 should include --wiki flag, got: %s", mock.streamingCalls[2])
	}
}

func TestRunSearchIndex_StartOverFlag(t *testing.T) {
	mock := &mockOrch{execOutput: "exists"}
	err := runSearchIndexWith(mock, "/tmp/test", "mywiki", true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !strings.Contains(mock.streamingCalls[0], "--startOver") {
		t.Errorf("step 1 should use --startOver flag, got: %s", mock.streamingCalls[0])
	}
	if strings.Contains(mock.streamingCalls[0], "--reindexAndRemoveOk") {
		t.Error("step 1 should not use --reindexAndRemoveOk when --start-over is set")
	}
}

func TestRunSearchIndex_NoWikiID(t *testing.T) {
	mock := &mockOrch{execOutput: "exists"}
	err := runSearchIndexWith(mock, "/tmp/test", "", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	for i, call := range mock.streamingCalls {
		if strings.Contains(call, "--wiki=") {
			t.Errorf("step %d should not include --wiki flag when wikiID is empty, got: %s", i+1, call)
		}
	}
}

func TestRunSearchIndex_StreamingError(t *testing.T) {
	mock := &mockOrch{
		execOutput:   "exists",
		streamingErr: fmt.Errorf("connection refused"),
	}
	err := runSearchIndexWith(mock, "/tmp/test", "mywiki", false)
	if err == nil {
		t.Fatal("expected error when streaming fails")
	}
	if !strings.Contains(err.Error(), "UpdateSearchIndexConfig.php failed") {
		t.Errorf("error should mention UpdateSearchIndexConfig.php, got: %v", err)
	}
}

func TestRunSearchIndex_WikiMsgInError(t *testing.T) {
	mock := &mockOrch{execOutput: ""}
	err := runSearchIndexWith(mock, "/tmp/test", "docs", false)
	if err == nil {
		t.Fatal("expected error")
	}
	if !strings.Contains(err.Error(), "for wiki 'docs'") {
		t.Errorf("error should mention wiki ID, got: %v", err)
	}
}
