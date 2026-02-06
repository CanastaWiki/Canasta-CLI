package compatibility

import (
	_ "embed"
	"fmt"

	"gopkg.in/yaml.v2"
)

//go:embed compatibility.yaml
var compatibilityYAML []byte

// OrchestratorConfig defines compatibility settings for an orchestrator
type OrchestratorConfig struct {
	Repo       string `yaml:"repo"`
	MinVersion string `yaml:"minVersion"`
	Tag        string `yaml:"tag"`
}

// ImageConfig defines the Docker image settings
type ImageConfig struct {
	Tag string `yaml:"tag"`
}

// Manifest represents the compatibility manifest structure
type Manifest struct {
	Orchestrators map[string]OrchestratorConfig `yaml:"orchestrators"`
	Image         ImageConfig                   `yaml:"image"`
}

var manifest *Manifest

// GetManifest returns the parsed compatibility manifest
func GetManifest() (*Manifest, error) {
	if manifest != nil {
		return manifest, nil
	}

	manifest = &Manifest{}
	if err := yaml.Unmarshal(compatibilityYAML, manifest); err != nil {
		return nil, fmt.Errorf("failed to parse compatibility manifest: %w", err)
	}

	return manifest, nil
}

// GetImageTag returns the Docker image tag from the compatibility manifest
func GetImageTag() (string, error) {
	m, err := GetManifest()
	if err != nil {
		return "", err
	}
	return m.Image.Tag, nil
}

// GetOrchestratorTag returns the repository tag for the specified orchestrator
func GetOrchestratorTag(orchestrator string) (string, error) {
	m, err := GetManifest()
	if err != nil {
		return "", err
	}

	config, ok := m.Orchestrators[orchestrator]
	if !ok {
		return "", fmt.Errorf("orchestrator %s not found in compatibility manifest", orchestrator)
	}

	return config.Tag, nil
}

// GetOrchestratorRepo returns the repository URL for the specified orchestrator
func GetOrchestratorRepo(orchestrator string) (string, error) {
	m, err := GetManifest()
	if err != nil {
		return "", err
	}

	config, ok := m.Orchestrators[orchestrator]
	if !ok {
		return "", fmt.Errorf("orchestrator %s not found in compatibility manifest", orchestrator)
	}

	return config.Repo, nil
}
