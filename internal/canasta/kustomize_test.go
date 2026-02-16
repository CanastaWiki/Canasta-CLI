package canasta

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGenerateKustomization(t *testing.T) {
	dir := t.TempDir()

	err := GenerateKustomization(dir, "my-wiki", "")
	if err != nil {
		t.Fatalf("GenerateKustomization() error = %v", err)
	}

	outputPath := filepath.Join(dir, "kustomization.yaml")
	data, err := os.ReadFile(outputPath)
	if err != nil {
		t.Fatalf("failed to read generated file: %v", err)
	}

	content := string(data)

	if !strings.Contains(content, "namespace: my-wiki") {
		t.Error("expected 'namespace: my-wiki' in output")
	}
	if !strings.Contains(content, "apiVersion: kustomize.config.k8s.io/v1beta1") {
		t.Error("expected kustomize apiVersion in output")
	}
	if !strings.Contains(content, "kubernetes/web.yaml") {
		t.Error("expected kubernetes/web.yaml resource in output")
	}
	if !strings.Contains(content, "configMapGenerator") {
		t.Error("expected configMapGenerator in output")
	}
	if strings.Contains(content, "images:") {
		t.Error("expected no images section when image is empty")
	}
}

func TestGenerateKustomizationWithImage(t *testing.T) {
	dir := t.TempDir()

	err := GenerateKustomization(dir, "my-wiki", "localhost:5000/canasta:local")
	if err != nil {
		t.Fatalf("GenerateKustomization() error = %v", err)
	}

	data, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatalf("failed to read generated file: %v", err)
	}

	content := string(data)

	if !strings.Contains(content, "images:") {
		t.Error("expected images section when image is set")
	}
	if !strings.Contains(content, "newName: localhost:5000/canasta:local") {
		t.Error("expected newName with registry image")
	}
}

func TestGenerateKustomizationOverwrites(t *testing.T) {
	dir := t.TempDir()

	// Generate first time
	if err := GenerateKustomization(dir, "first-ns", ""); err != nil {
		t.Fatalf("first GenerateKustomization() error = %v", err)
	}

	// Generate second time with different namespace
	if err := GenerateKustomization(dir, "second-ns", ""); err != nil {
		t.Fatalf("second GenerateKustomization() error = %v", err)
	}

	data, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatalf("failed to read file: %v", err)
	}

	content := string(data)
	if strings.Contains(content, "first-ns") {
		t.Error("old namespace should be overwritten")
	}
	if !strings.Contains(content, "namespace: second-ns") {
		t.Error("expected 'namespace: second-ns' in output")
	}
}
