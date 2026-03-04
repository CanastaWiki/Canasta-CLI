package upgrade

import "testing"

func TestComposeVolumeName(t *testing.T) {
	tests := []struct {
		installPath string
		suffix      string
		want        string
	}{
		{"/opt/canasta/my-wiki", "db", "my-wiki_db"},
		{"/opt/canasta/MyWiki", "db", "mywiki_db"},
		{"/opt/canasta/my wiki", "db", "mywiki_db"},
		{"/opt/canasta/my_wiki!", "db", "my_wiki_db"},
		{"/opt/canasta/MY-WIKI", "mediawiki-uploads", "my-wiki_mediawiki-uploads"},
		{"/single", "db", "single_db"},
		{"/path/with spaces/wiki", "db", "wiki_db"},
		{"", "db", "_db"},
	}
	for _, tt := range tests {
		got := composeVolumeName(tt.installPath, tt.suffix)
		if got != tt.want {
			t.Errorf("composeVolumeName(%q, %q) = %q, want %q", tt.installPath, tt.suffix, got, tt.want)
		}
	}
}
