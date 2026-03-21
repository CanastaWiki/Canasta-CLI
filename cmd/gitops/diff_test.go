package gitops

import (
	"reflect"
	"testing"
)

func TestClassifyChanges(t *testing.T) {
	tests := []struct {
		name       string
		local      []string
		remote     []string
		wantLocal  []string
		wantRemote []string
		wantConfl  []string
	}{
		{
			name:       "local only",
			local:      []string{"a.php", "b.php"},
			remote:     nil,
			wantLocal:  []string{"a.php", "b.php"},
			wantRemote: nil,
			wantConfl:  nil,
		},
		{
			name:       "remote only",
			local:      nil,
			remote:     []string{"c.php", "d.php"},
			wantLocal:  nil,
			wantRemote: []string{"c.php", "d.php"},
			wantConfl:  nil,
		},
		{
			name:       "no overlap",
			local:      []string{"a.php"},
			remote:     []string{"b.php"},
			wantLocal:  []string{"a.php"},
			wantRemote: []string{"b.php"},
			wantConfl:  nil,
		},
		{
			name:       "all conflict",
			local:      []string{"a.php", "b.php"},
			remote:     []string{"b.php", "a.php"},
			wantLocal:  nil,
			wantRemote: nil,
			wantConfl:  []string{"a.php", "b.php"},
		},
		{
			name:       "mixed",
			local:      []string{"config/LocalSettings.php", "docker-compose.yml", ".env"},
			remote:     []string{"config/LocalSettings.php", "config/wikis.yaml"},
			wantLocal:  []string{".env", "docker-compose.yml"},
			wantRemote: []string{"config/wikis.yaml"},
			wantConfl:  []string{"config/LocalSettings.php"},
		},
		{
			name:       "both empty",
			local:      nil,
			remote:     nil,
			wantLocal:  nil,
			wantRemote: nil,
			wantConfl:  nil,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			localOnly, remoteOnly, conflicts := classifyChanges(tt.local, tt.remote)
			if !reflect.DeepEqual(localOnly, tt.wantLocal) {
				t.Errorf("localOnly = %v, want %v", localOnly, tt.wantLocal)
			}
			if !reflect.DeepEqual(remoteOnly, tt.wantRemote) {
				t.Errorf("remoteOnly = %v, want %v", remoteOnly, tt.wantRemote)
			}
			if !reflect.DeepEqual(conflicts, tt.wantConfl) {
				t.Errorf("conflicts = %v, want %v", conflicts, tt.wantConfl)
			}
		})
	}
}
