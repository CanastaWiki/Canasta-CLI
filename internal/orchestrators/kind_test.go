package orchestrators

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestKindClusterName(t *testing.T) {
	tests := []struct {
		canastaID string
		want      string
	}{
		{"myinstance", "canasta-myinstance"},
		{"test-k8s", "canasta-test-k8s"},
		{"a", "canasta-a"},
		{"wiki_prod", "canasta-wiki_prod"},
	}
	for _, tt := range tests {
		got := KindClusterName(tt.canastaID)
		if got != tt.want {
			t.Errorf("KindClusterName(%q) = %q, want %q", tt.canastaID, got, tt.want)
		}
	}
}

func TestRenderKindConfig(t *testing.T) {
	tests := []struct {
		name string
		data kindConfigData
		want []string // strings that must appear in the output
	}{
		{
			name: "default ports",
			data: kindConfigData{
				ClusterName:   "canasta-test",
				HTTPNodePort:  30080,
				HTTPSNodePort: 30443,
				HTTPPort:      80,
				HTTPSPort:     443,
			},
			want: []string{
				"name: canasta-test",
				"hostPort: 80",
				"hostPort: 443",
				"containerPort: 30080",
				"containerPort: 30443",
			},
		},
		{
			name: "custom ports",
			data: kindConfigData{
				ClusterName:   "canasta-dev",
				HTTPNodePort:  30080,
				HTTPSNodePort: 30443,
				HTTPPort:      8080,
				HTTPSPort:     8443,
			},
			want: []string{
				"name: canasta-dev",
				"hostPort: 8080",
				"hostPort: 8443",
				"containerPort: 30080",
				"containerPort: 30443",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := renderKindConfig(tt.data)
			if err != nil {
				t.Fatalf("renderKindConfig() error = %v", err)
			}
			for _, s := range tt.want {
				if !strings.Contains(got, s) {
					t.Errorf("renderKindConfig() output missing %q, got:\n%s", s, got)
				}
			}
			// Verify it's valid YAML-like content
			if !strings.Contains(got, "kind: Cluster") {
				t.Error("renderKindConfig() output missing 'kind: Cluster'")
			}
		})
	}
}

func TestGetPortsFromEnv(t *testing.T) {
	tests := []struct {
		name      string
		envContent string
		wantHTTP  int
		wantHTTPS int
	}{
		{
			name:       "defaults when no env file",
			envContent: "",
			wantHTTP:   80,
			wantHTTPS:  443,
		},
		{
			name:       "custom ports",
			envContent: "HTTP_PORT=8080\nHTTPS_PORT=8443\n",
			wantHTTP:   8080,
			wantHTTPS:  8443,
		},
		{
			name:       "only HTTP_PORT set",
			envContent: "HTTP_PORT=9090\n",
			wantHTTP:   9090,
			wantHTTPS:  443,
		},
		{
			name:       "only HTTPS_PORT set",
			envContent: "HTTPS_PORT=9443\n",
			wantHTTP:   80,
			wantHTTPS:  9443,
		},
		{
			name:       "invalid port value falls back to default",
			envContent: "HTTP_PORT=notanumber\nHTTPS_PORT=8443\n",
			wantHTTP:   80,
			wantHTTPS:  8443,
		},
		{
			name:       "empty values fall back to default",
			envContent: "HTTP_PORT=\nHTTPS_PORT=\n",
			wantHTTP:   80,
			wantHTTPS:  443,
		},
		{
			name:       "ports with other env vars",
			envContent: "MYSQL_PASSWORD=secret\nHTTP_PORT=8080\nHTTPS_PORT=8443\nMW_SECRET_KEY=abc123\n",
			wantHTTP:   8080,
			wantHTTPS:  8443,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			if tt.envContent != "" {
				if err := os.WriteFile(filepath.Join(dir, ".env"), []byte(tt.envContent), 0644); err != nil {
					t.Fatal(err)
				}
			}
			gotHTTP, gotHTTPS := GetPortsFromEnv(dir)
			if gotHTTP != tt.wantHTTP {
				t.Errorf("GetPortsFromEnv() httpPort = %d, want %d", gotHTTP, tt.wantHTTP)
			}
			if gotHTTPS != tt.wantHTTPS {
				t.Errorf("GetPortsFromEnv() httpsPort = %d, want %d", gotHTTPS, tt.wantHTTPS)
			}
		})
	}
}
