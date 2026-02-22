package orchestrators

import (
	"bytes"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"text/template"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators/kubernetes"
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

func (k *KubernetesOrchestrator) WriteStackFiles(installPath string) error {
	return k.walkStackFiles(installPath, false)
}

func (k *KubernetesOrchestrator) UpdateStackFiles(installPath string, dryRun bool) (bool, error) {
	changed := false
	err := fs.WalkDir(kubernetes.StackFiles, "files/kubernetes", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relPath, err := filepath.Rel("files", path)
		if err != nil {
			return err
		}
		if relPath == "." {
			return nil
		}
		targetPath := filepath.Join(installPath, relPath)
		if d.IsDir() {
			return os.MkdirAll(targetPath, 0755)
		}
		if d.Name() == ".gitkeep" {
			return nil
		}
		embedded, err := kubernetes.StackFiles.ReadFile(path)
		if err != nil {
			return fmt.Errorf("failed to read embedded file %s: %w", path, err)
		}
		existing, readErr := os.ReadFile(targetPath)
		if readErr == nil && bytes.Equal(existing, embedded) {
			return nil // unchanged
		}
		changed = true
		if dryRun {
			if readErr != nil {
				fmt.Printf("  Would create %s\n", relPath)
			} else {
				fmt.Printf("  Would update %s\n", relPath)
			}
			return nil
		}
		if readErr != nil {
			fmt.Printf("  Creating %s\n", relPath)
		} else {
			fmt.Printf("  Updating %s\n", relPath)
		}
		return os.WriteFile(targetPath, embedded, 0644)
	})
	return changed, err
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
	tmplData, err := kubernetes.StackFiles.ReadFile("files/kustomization.yaml.tmpl")
	if err != nil {
		return fmt.Errorf("failed to read embedded kustomization template: %w", err)
	}
	tmpl, err := template.New("kustomization").Parse(string(tmplData))
	if err != nil {
		return fmt.Errorf("failed to parse kustomization template: %w", err)
	}
	namespace := filepath.Base(installPath)
	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, struct{ Namespace string }{namespace}); err != nil {
		return fmt.Errorf("failed to execute kustomization template: %w", err)
	}
	if err := os.WriteFile(filepath.Join(installPath, "kustomization.yaml"), buf.Bytes(), 0644); err != nil {
		return err
	}
	if err := canasta.CreateCaddyfileSite(installPath); err != nil {
		return err
	}
	if err := canasta.CreateCaddyfileGlobal(installPath); err != nil {
		return err
	}
	return canasta.RewriteCaddy(installPath)
}

func (k *KubernetesOrchestrator) UpdateConfig(installPath string) error {
	return nil // K8s config is managed via kustomization overlays
}

func (k *KubernetesOrchestrator) MigrateConfig(installPath string, dryRun bool) (bool, error) {
	return false, nil // No Compose-specific migrations needed for K8s
}

// walkStackFiles walks the embedded K8s manifest files and writes them to installPath.
// Only manifest YAMLs under files/kubernetes/ are written; the template is skipped.
// If overwrite is false (create mode), existing files are skipped.
func (k *KubernetesOrchestrator) walkStackFiles(installPath string, overwrite bool) error {
	return fs.WalkDir(kubernetes.StackFiles, "files/kubernetes", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relPath, err := filepath.Rel("files", path)
		if err != nil {
			return err
		}
		if relPath == "." {
			return nil
		}
		targetPath := filepath.Join(installPath, relPath)
		if d.IsDir() {
			return os.MkdirAll(targetPath, 0755)
		}
		if d.Name() == ".gitkeep" {
			return nil
		}
		if !overwrite {
			if _, err := os.Stat(targetPath); err == nil {
				return nil // no-clobber
			}
		}
		data, err := kubernetes.StackFiles.ReadFile(path)
		if err != nil {
			return fmt.Errorf("failed to read embedded file %s: %w", path, err)
		}
		return os.WriteFile(targetPath, data, 0644)
	})
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
