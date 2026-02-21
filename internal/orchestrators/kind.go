package orchestrators

import (
	"bytes"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"text/template"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators/kubernetes"
)

// Default NodePort values used in the caddy service patch.
const (
	defaultHTTPNodePort  = 30080
	defaultHTTPSNodePort = 30443
)

// kindConfigData holds template values for kind cluster configuration.
type kindConfigData struct {
	ClusterName   string
	HTTPNodePort  int
	HTTPSNodePort int
	HTTPPort      int
	HTTPSPort     int
}

// KindClusterName returns the kind cluster name for a Canasta installation.
func KindClusterName(canastaID string) string {
	return "canasta-" + canastaID
}

// kindClusterExists checks whether a kind cluster with the given name exists.
func kindClusterExists(clusterName string) (bool, error) {
	cmd := exec.Command("kind", "get", "clusters")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return false, fmt.Errorf("failed to list kind clusters: %s", strings.TrimSpace(string(output)))
	}
	for _, line := range strings.Split(strings.TrimSpace(string(output)), "\n") {
		if strings.TrimSpace(line) == clusterName {
			return true, nil
		}
	}
	return false, nil
}

// renderKindConfig renders the kind cluster config template with the given parameters.
func renderKindConfig(data kindConfigData) (string, error) {
	tmplBytes, err := kubernetes.StackFiles.ReadFile("files/kind-config.yaml.tmpl")
	if err != nil {
		return "", fmt.Errorf("failed to read kind config template: %w", err)
	}
	tmpl, err := template.New("kind-config").Parse(string(tmplBytes))
	if err != nil {
		return "", fmt.Errorf("failed to parse kind config template: %w", err)
	}
	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("failed to render kind config template: %w", err)
	}
	return buf.String(), nil
}

// CreateKindCluster creates a new kind cluster with port mappings.
// Returns an error if a cluster with the same name already exists.
func CreateKindCluster(clusterName string, httpPort, httpsPort int) error {
	exists, err := kindClusterExists(clusterName)
	if err != nil {
		return err
	}
	if exists {
		return fmt.Errorf("kind cluster %q already exists", clusterName)
	}

	configYAML, err := renderKindConfig(kindConfigData{
		ClusterName:   clusterName,
		HTTPNodePort:  defaultHTTPNodePort,
		HTTPSNodePort: defaultHTTPSNodePort,
		HTTPPort:      httpPort,
		HTTPSPort:     httpsPort,
	})
	if err != nil {
		return err
	}

	logging.Print(fmt.Sprintf("Creating kind cluster %q...\n", clusterName))
	cmd := exec.Command("kind", "create", "cluster", "--config", "-")
	cmd.Stdin = strings.NewReader(configYAML)
	output, err := cmd.CombinedOutput()
	logging.Print(string(output))
	if err != nil {
		return fmt.Errorf("failed to create kind cluster: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

// DeleteKindCluster deletes a kind cluster. No-op if the cluster doesn't exist.
func DeleteKindCluster(clusterName string) error {
	exists, err := kindClusterExists(clusterName)
	if err != nil {
		return err
	}
	if !exists {
		return nil
	}

	logging.Print(fmt.Sprintf("Deleting kind cluster %q...\n", clusterName))
	cmd := exec.Command("kind", "delete", "cluster", "--name", clusterName)
	output, err := cmd.CombinedOutput()
	logging.Print(string(output))
	if err != nil {
		return fmt.Errorf("failed to delete kind cluster: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

// EnsureKindCluster creates the kind cluster if it doesn't exist, or sets the
// kubectl context if it does. Returns true if the cluster was newly created.
func EnsureKindCluster(clusterName string, httpPort, httpsPort int) (bool, error) {
	exists, err := kindClusterExists(clusterName)
	if err != nil {
		return false, err
	}
	if exists {
		if err := setKindKubectlContext(clusterName); err != nil {
			return false, err
		}
		return false, nil
	}
	if err := CreateKindCluster(clusterName, httpPort, httpsPort); err != nil {
		return false, err
	}
	return true, nil
}

// ensureKindContext looks up the kind cluster name for the installation at
// installPath and sets the kubectl context if it's a kind-managed cluster.
// This is a no-op for non-kind installations or if the lookup fails.
func ensureKindContext(installPath string) error {
	id, err := config.GetCanastaID(installPath)
	if err != nil {
		return nil
	}
	inst, err := config.GetDetails(id)
	if err != nil {
		return nil
	}
	if inst.KindCluster != "" {
		return setKindKubectlContext(inst.KindCluster)
	}
	return nil
}

// setKindKubectlContext switches the kubectl context to the given kind cluster.
func setKindKubectlContext(clusterName string) error {
	contextName := "kind-" + clusterName
	cmd := exec.Command("kubectl", "config", "use-context", contextName)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to set kubectl context to %s: %s", contextName, strings.TrimSpace(string(output)))
	}
	return nil
}

// LoadImageToKind loads a local Docker image into a kind cluster so that
// pods can pull it without a registry.
func LoadImageToKind(clusterName, imageTag string) error {
	logging.Print(fmt.Sprintf("Loading image %s into kind cluster %s...\n", imageTag, clusterName))
	cmd := exec.Command("kind", "load", "docker-image", imageTag, "--name", clusterName)
	output, err := cmd.CombinedOutput()
	logging.Print(string(output))
	if err != nil {
		return fmt.Errorf("failed to load image into kind: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

// GetPortsFromEnv reads HTTP_PORT and HTTPS_PORT from the .env file at the
// given installation path, returning defaults of 80 and 443 if not set.
func GetPortsFromEnv(installPath string) (httpPort int, httpsPort int) {
	httpPort = 80
	httpsPort = 443

	envPath := installPath + "/.env"
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return
	}
	if v, ok := envVars["HTTP_PORT"]; ok && v != "" {
		if p, err := strconv.Atoi(v); err == nil {
			httpPort = p
		}
	}
	if v, ok := envVars["HTTPS_PORT"]; ok && v != "" {
		if p, err := strconv.Atoi(v); err == nil {
			httpsPort = p
		}
	}
	return
}
