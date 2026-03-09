package orchestrators

import (
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

func TestBackupVolumeName(t *testing.T) {
	tests := []struct {
		installPath string
		want        string
	}{
		{"/opt/canasta/my-wiki", "canasta-backup-my-wiki"},
		{"/home/user/test", "canasta-backup-test"},
		{"/single", "canasta-backup-single"},
	}
	for _, tt := range tests {
		got := backupVolumeName(tt.installPath)
		if got != tt.want {
			t.Errorf("backupVolumeName(%q) = %q, want %q", tt.installPath, got, tt.want)
		}
	}
}

// captureShellArg installs a mock execute.Run that records the argument
// passed to "sh -c" and returns it. The mock succeeds without error.
func captureShellArg(t *testing.T) *string {
	t.Helper()
	var captured string
	execute.Run = func(_, _ string, args ...string) (string, error) {
		for i, a := range args {
			if a == "-c" && i > 0 && args[i-1] == "sh" && i+1 < len(args) {
				captured = args[i+1]
			}
		}
		return "", nil
	}
	t.Cleanup(execute.ResetForTesting)
	return &captured
}

func TestStageToVolumeNoQuotesAroundShellCmd(t *testing.T) {
	shellArg := captureShellArg(t)

	volumes := map[string]string{
		"/host/images": "/currentsnapshot/images",
	}
	if err := stageToVolume("testvol", volumes); err != nil {
		t.Fatalf("stageToVolume returned error: %v", err)
	}

	if strings.HasPrefix(*shellArg, "'") || strings.HasSuffix(*shellArg, "'") {
		t.Errorf("shell command should not be wrapped in single quotes, got: %s", *shellArg)
	}
	if !strings.HasPrefix(*shellArg, "rm -rf /currentsnapshot/*") {
		t.Errorf("unexpected shell command: %s", *shellArg)
	}
}

func TestRestoreFromVolumeNoQuotesAroundShellCmd(t *testing.T) {
	shellArg := captureShellArg(t)

	dirs := map[string]string{
		"/currentsnapshot/config": "/opt/wiki/config",
	}
	if err := restoreFromVolume("testvol", "/opt/wiki", dirs); err != nil {
		t.Fatalf("restoreFromVolume returned error: %v", err)
	}

	if strings.HasPrefix(*shellArg, "'") || strings.HasSuffix(*shellArg, "'") {
		t.Errorf("shell command should not be wrapped in single quotes, got: %s", *shellArg)
	}
	if !strings.Contains(*shellArg, "/currentsnapshot/config") {
		t.Errorf("unexpected shell command: %s", *shellArg)
	}
}
