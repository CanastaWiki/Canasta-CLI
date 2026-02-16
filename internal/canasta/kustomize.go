package canasta

import (
	_ "embed"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"text/template"
)

//go:embed files/kustomization.yaml.tmpl
var kustomizationTemplate string

type kustomizationData struct {
	Namespace string
}

// GenerateKustomization creates a kustomization.yaml in the installation directory.
// The namespace corresponds to the installation ID for K8s isolation.
func GenerateKustomization(installPath, namespace string) error {
	tmpl, err := template.New("kustomization").Parse(kustomizationTemplate)
	if err != nil {
		return fmt.Errorf("failed to parse kustomization template: %w", err)
	}

	data := kustomizationData{
		Namespace: namespace,
	}

	outputPath := filepath.Join(installPath, "kustomization.yaml")
	var buf strings.Builder
	if err := tmpl.Execute(&buf, data); err != nil {
		return fmt.Errorf("failed to execute kustomization template: %w", err)
	}

	if err := os.WriteFile(outputPath, []byte(buf.String()), 0644); err != nil {
		return fmt.Errorf("failed to write kustomization.yaml: %w", err)
	}
	return nil
}
