package orchestrators

import (
	"os"
	"path/filepath"
	"testing"
)

func TestGetNamespaceFromPath(t *testing.T) {
	tests := []struct {
		name      string
		content   string
		wantNS    string
		wantErr   bool
	}{
		{
			name:    "standard namespace",
			content: "namespace: my-wiki\nresources:\n  - kubernetes/\n",
			wantNS:  "my-wiki",
			wantErr: false,
		},
		{
			name:    "namespace with extra spaces",
			content: "namespace:   test-ns  \nresources:\n",
			wantNS:  "test-ns",
			wantErr: false,
		},
		{
			name:    "namespace not first line",
			content: "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nnamespace: wiki-prod\n",
			wantNS:  "wiki-prod",
			wantErr: false,
		},
		{
			name:    "missing namespace",
			content: "resources:\n  - kubernetes/\n",
			wantNS:  "",
			wantErr: true,
		},
		{
			name:    "empty namespace value",
			content: "namespace:\nresources:\n",
			wantNS:  "",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			err := os.WriteFile(filepath.Join(dir, "kustomization.yaml"), []byte(tt.content), 0644)
			if err != nil {
				t.Fatalf("failed to write test file: %v", err)
			}

			ns, err := getNamespaceFromPath(dir)
			if (err != nil) != tt.wantErr {
				t.Errorf("getNamespaceFromPath() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if ns != tt.wantNS {
				t.Errorf("getNamespaceFromPath() = %q, want %q", ns, tt.wantNS)
			}
		})
	}
}

func TestGetNamespaceFromPathMissingFile(t *testing.T) {
	dir := t.TempDir()
	_, err := getNamespaceFromPath(dir)
	if err == nil {
		t.Fatal("expected error for missing kustomization.yaml")
	}
}

func TestNewKubernetesReturnsOrchestrator(t *testing.T) {
	for _, id := range []string{"kubernetes", "k8s"} {
		orch, err := New(id)
		if err != nil {
			t.Fatalf("New(%q) unexpected error: %v", id, err)
		}
		if orch == nil {
			t.Fatalf("New(%q) returned nil orchestrator", id)
		}
		if _, ok := orch.(*KubernetesOrchestrator); !ok {
			t.Errorf("New(%q) returned %T, want *KubernetesOrchestrator", id, orch)
		}
	}
}
