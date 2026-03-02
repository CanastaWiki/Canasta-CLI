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

func TestComposeVolumeName(t *testing.T) {
	tests := []struct {
		installPath string
		suffix      string
		want        string
	}{
		{"/opt/canasta/my-wiki", "db", "my-wiki_db"},
		{"/opt/canasta/my-wiki", "mediawiki-uploads", "my-wiki_mediawiki-uploads"},
		{"/single", "db", "single_db"},
		{"", "db", "._db"},
		{"/path/with spaces/wiki", "db", "wiki_db"},
	}
	for _, tt := range tests {
		got := composeVolumeName(tt.installPath, tt.suffix)
		if got != tt.want {
			t.Errorf("composeVolumeName(%q, %q) = %q, want %q", tt.installPath, tt.suffix, got, tt.want)
		}
	}
}
