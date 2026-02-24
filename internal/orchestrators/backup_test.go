package orchestrators

import "testing"

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
