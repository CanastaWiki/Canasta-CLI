package orchestrators

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	yaml "gopkg.in/yaml.v2"
)

// webDeployment is the Kubernetes deployment name for the MediaWiki web service.
const webDeployment = "deployment/web"

// podLabelKey is the label used to identify pods belonging to a service.
const podLabelKey = "app"

// KubernetesOrchestrator implements Orchestrator using kubectl.
// Each Canasta installation maps to a Kubernetes namespace.
type KubernetesOrchestrator struct{}

func (k *KubernetesOrchestrator) CheckDependencies() error {
	if _, err := exec.LookPath("kubectl"); err != nil {
		return fmt.Errorf("kubectl must be installed and in PATH")
	}
	cmd := exec.Command("kubectl", "cluster-info")
	if output, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("cannot connect to Kubernetes cluster: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

func (k *KubernetesOrchestrator) GetRepoLink() string {
	return "https://github.com/CanastaWiki/Canasta-Kubernetes.git"
}

func (k *KubernetesOrchestrator) Start(instance config.Installation) error {
	ns, err := getNamespaceFromPath(instance.Path)
	if err != nil {
		return err
	}
	logging.Print("Applying Kubernetes manifests\n")

	cmd := exec.Command("kubectl", "apply", "-k", ".")
	cmd.Dir = instance.Path
	output, err := cmd.CombinedOutput()
	logging.Print(string(output))
	if err != nil {
		return fmt.Errorf("kubectl apply failed: %s", strings.TrimSpace(string(output)))
	}

	// Wait for the web deployment to be ready
	rolloutCmd := exec.Command("kubectl", "rollout", "status",
		webDeployment, "-n", ns, "--timeout=300s")
	rolloutOutput, err := rolloutCmd.CombinedOutput()
	logging.Print(string(rolloutOutput))
	if err != nil {
		return fmt.Errorf("web deployment rollout failed: %s", strings.TrimSpace(string(rolloutOutput)))
	}
	return nil
}

func (k *KubernetesOrchestrator) Stop(instance config.Installation) error {
	ns, err := getNamespaceFromPath(instance.Path)
	if err != nil {
		return err
	}
	logging.Print("Scaling down all deployments\n")

	cmd := exec.Command("kubectl", "scale", "deployment", "--all",
		"--replicas=0", "-n", ns)
	output, err := cmd.CombinedOutput()
	logging.Print(string(output))
	if err != nil {
		return fmt.Errorf("failed to scale down deployments: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

func (k *KubernetesOrchestrator) Update(installPath string) (*UpdateReport, error) {
	ns, err := getNamespaceFromPath(installPath)
	if err != nil {
		return nil, err
	}

	cmd := exec.Command("kubectl", "rollout", "restart", webDeployment, "-n", ns)
	output, err := cmd.CombinedOutput()
	logging.Print(string(output))
	if err != nil {
		return nil, fmt.Errorf("rollout restart failed: %s", strings.TrimSpace(string(output)))
	}

	rolloutCmd := exec.Command("kubectl", "rollout", "status",
		webDeployment, "-n", ns, "--timeout=300s")
	rolloutOutput, err := rolloutCmd.CombinedOutput()
	logging.Print(string(rolloutOutput))
	if err != nil {
		return nil, fmt.Errorf("rollout status failed: %s", strings.TrimSpace(string(rolloutOutput)))
	}

	return &UpdateReport{}, nil
}

func (k *KubernetesOrchestrator) Destroy(installPath string) (string, error) {
	ns, err := getNamespaceFromPath(installPath)
	if err != nil {
		return "", err
	}

	cmd := exec.Command("kubectl", "delete", "namespace", ns, "--ignore-not-found")
	output, err := cmd.CombinedOutput()
	logging.Print(string(output))
	if err != nil {
		return string(output), fmt.Errorf("failed to delete namespace: %s", strings.TrimSpace(string(output)))
	}
	return string(output), nil
}

func (k *KubernetesOrchestrator) ExecWithError(installPath, service, command string) (string, error) {
	ns, err := getNamespaceFromPath(installPath)
	if err != nil {
		return "", err
	}

	pod, err := getRunningPod(ns, service)
	if err != nil {
		return "", err
	}

	cmd := exec.Command("kubectl", "exec", pod, "-n", ns,
		"--", "/bin/bash", "-c", command)
	outputByte, err := cmd.CombinedOutput()
	output := string(outputByte)
	logging.Print(output)
	return output, err
}

func (k *KubernetesOrchestrator) ExecStreaming(installPath, service, command string) error {
	ns, err := getNamespaceFromPath(installPath)
	if err != nil {
		return err
	}

	pod, err := getRunningPod(ns, service)
	if err != nil {
		return err
	}

	cmd := exec.Command("kubectl", "exec", pod, "-n", ns,
		"--", "/bin/bash", "-c", command)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("command failed: %w", err)
	}
	return nil
}

func (k *KubernetesOrchestrator) CheckRunningStatus(instance config.Installation) error {
	ns, err := getNamespaceFromPath(instance.Path)
	if err != nil {
		return err
	}

	cmd := exec.Command("kubectl", "get", webDeployment, "-n", ns,
		"-o", "jsonpath={.status.readyReplicas}")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to check deployment status: %s", strings.TrimSpace(string(output)))
	}

	replicas := strings.TrimSpace(string(output))
	if replicas == "" || replicas == "0" {
		return fmt.Errorf("web deployment has no ready replicas")
	}
	return nil
}

func (k *KubernetesOrchestrator) CopyFrom(installPath, service, containerPath, hostPath string) error {
	ns, err := getNamespaceFromPath(installPath)
	if err != nil {
		return err
	}

	pod, err := getRunningPod(ns, service)
	if err != nil {
		return err
	}

	src := fmt.Sprintf("%s/%s:%s", ns, pod, containerPath)
	cmd := exec.Command("kubectl", "cp", src, hostPath)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to copy from pod: %s - %w", strings.TrimSpace(string(output)), err)
	}
	return nil
}

func (k *KubernetesOrchestrator) CopyTo(installPath, service, hostPath, containerPath string) error {
	ns, err := getNamespaceFromPath(installPath)
	if err != nil {
		return err
	}

	pod, err := getRunningPod(ns, service)
	if err != nil {
		return err
	}

	dest := fmt.Sprintf("%s/%s:%s", ns, pod, containerPath)
	cmd := exec.Command("kubectl", "cp", hostPath, dest)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to copy to pod: %s - %w", strings.TrimSpace(string(output)), err)
	}
	return nil
}

func (k *KubernetesOrchestrator) RunBackup(installPath, envPath string, volumes map[string]string, args ...string) (string, error) {
	return "", fmt.Errorf("backup is not yet supported for Kubernetes installations")
}

func (k *KubernetesOrchestrator) RestoreFromBackupVolume(installPath string, dirs map[string]string) error {
	return fmt.Errorf("backup is not yet supported for Kubernetes installations")
}

func (k *KubernetesOrchestrator) InitConfig(installPath string) error {
	return nil // K8s config is managed via kustomization overlays
}

func (k *KubernetesOrchestrator) UpdateConfig(installPath string) error {
	return nil // K8s config is managed via kustomization overlays
}

func (k *KubernetesOrchestrator) MigrateConfig(installPath string, dryRun bool) (bool, error) {
	return false, nil // No Compose-specific migrations needed for K8s
}

// getRunningPod finds a running pod for the given service label in a namespace.
func getRunningPod(namespace, service string) (string, error) {
	labelSelector := fmt.Sprintf("%s=%s", podLabelKey, service)
	cmd := exec.Command("kubectl", "get", "pods",
		"-n", namespace,
		"-l", labelSelector,
		"--field-selector=status.phase=Running",
		"-o", "jsonpath={.items[0].metadata.name}")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("no running pod found for service %q in namespace %q: %s",
			service, namespace, strings.TrimSpace(string(output)))
	}
	podName := strings.TrimSpace(string(output))
	if podName == "" {
		return "", fmt.Errorf("no running pod found for service %q in namespace %q", service, namespace)
	}
	return podName, nil
}

// kustomizationFile is the minimal structure needed to extract the namespace
// from a kustomization.yaml file.
type kustomizationFile struct {
	Namespace string `yaml:"namespace"`
}

// getNamespaceFromPath reads the namespace from the kustomization.yaml
// in the installation directory.
func getNamespaceFromPath(installPath string) (string, error) {
	kustomizePath := filepath.Join(installPath, "kustomization.yaml")
	data, err := os.ReadFile(kustomizePath)
	if err != nil {
		return "", fmt.Errorf("cannot read kustomization.yaml: %w", err)
	}

	var kf kustomizationFile
	if err := yaml.Unmarshal(data, &kf); err != nil {
		return "", fmt.Errorf("failed to parse kustomization.yaml: %w", err)
	}

	if kf.Namespace == "" {
		return "", fmt.Errorf("no namespace field found in kustomization.yaml")
	}
	return kf.Namespace, nil
}
