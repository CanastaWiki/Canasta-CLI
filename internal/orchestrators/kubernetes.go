package orchestrators

import (
	"bytes"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
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
type KubernetesOrchestrator struct {
	// LocalCluster enables NodePort exposure instead of LoadBalancer,
	// for use with local clusters (kind, k3d, minikube, etc.).
	LocalCluster bool
}

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
	if err := canasta.CreateCaddyfileSite(installPath); err != nil {
		return err
	}
	if err := canasta.CreateCaddyfileGlobal(installPath); err != nil {
		return err
	}
	if err := canasta.RewriteCaddy(installPath); err != nil {
		return err
	}
	return k.generateKustomization(installPath, k.LocalCluster)
}

func (k *KubernetesOrchestrator) UpdateConfig(installPath string) error {
	if err := canasta.RewriteCaddy(installPath); err != nil {
		return err
	}
	localCluster := false
	if id, err := config.GetCanastaID(installPath); err == nil {
		if inst, err := config.GetDetails(id); err == nil {
			localCluster = inst.LocalCluster
		}
	}
	return k.generateKustomization(installPath, localCluster)
}

// kustomization is the Go representation of kustomization.yaml.
type kustomization struct {
	APIVersion         string              `yaml:"apiVersion"`
	Kind               string              `yaml:"kind"`
	Namespace          string              `yaml:"namespace"`
	Resources          []string            `yaml:"resources"`
	ConfigMapGenerator []configMapEntry    `yaml:"configMapGenerator"`
	Patches            []kustomizePatch    `yaml:"patches,omitempty"`
}

type configMapEntry struct {
	Name  string   `yaml:"name"`
	Files []string `yaml:"files,omitempty"`
	Envs  []string `yaml:"envs,omitempty"`
}

type kustomizePatch struct {
	Patch string `yaml:"patch"`
}

// generateKustomization programmatically generates kustomization.yaml by
// scanning the installation directory for settings files and wikis.
func (k *KubernetesOrchestrator) generateKustomization(installPath string, localCluster bool) error {
	namespace := filepath.Base(installPath)

	kust := kustomization{
		APIVersion: "kustomize.config.k8s.io/v1beta1",
		Kind:       "Kustomization",
		Namespace:  namespace,
		Resources: []string{
			"kubernetes/namespace.yaml",
			"kubernetes/caddy.yaml",
			"kubernetes/db.yaml",
			"kubernetes/elasticsearch.yaml",
			"kubernetes/varnish.yaml",
			"kubernetes/web.yaml",
		},
	}

	// 1. Global settings ConfigMap
	globalFiles, err := scanSettingsDir(installPath, "config/settings/global")
	if err != nil {
		return fmt.Errorf("failed to scan global settings: %w", err)
	}
	kust.ConfigMapGenerator = append(kust.ConfigMapGenerator, configMapEntry{
		Name:  "canasta-settings-global",
		Files: globalFiles,
	})

	// 2. Per-wiki settings ConfigMaps + patches
	wikisYamlPath := filepath.Join(installPath, "config", "wikis.yaml")
	wikiIDs, _, _, err := farmsettings.ReadWikisYaml(wikisYamlPath)
	if err != nil {
		return fmt.Errorf("failed to read wikis.yaml: %w", err)
	}
	for _, wikiID := range wikiIDs {
		normalizedID := canasta.NormalizeWikiID(wikiID)
		relDir := filepath.Join("config", "settings", "wikis", normalizedID)
		absDir := filepath.Join(installPath, relDir)

		// Skip if the wiki settings directory doesn't exist
		if _, err := os.Stat(absDir); os.IsNotExist(err) {
			continue
		}

		wikiFiles, err := scanSettingsDir(installPath, relDir)
		if err != nil {
			return fmt.Errorf("failed to scan settings for wiki %q: %w", wikiID, err)
		}
		if len(wikiFiles) == 0 {
			continue
		}

		configMapName := "canasta-settings-wiki-" + normalizedID
		kust.ConfigMapGenerator = append(kust.ConfigMapGenerator, configMapEntry{
			Name:  configMapName,
			Files: wikiFiles,
		})
		kust.Patches = append(kust.Patches, buildWikiSettingsPatch(normalizedID, configMapName))
	}

	// 3. Static config files ConfigMap
	configFiles := []string{
		"config/wikis.yaml",
		"config/Caddyfile",
		"config/Caddyfile.site",
		"config/Caddyfile.global",
		"config/default.vcl",
		"my.cnf",
	}
	// Conditionally include LocalSettings.php
	if _, err := os.Stat(filepath.Join(installPath, "config", "LocalSettings.php")); err == nil {
		configFiles = append(configFiles, "config/LocalSettings.php")
		kust.Patches = append(kust.Patches, buildLocalSettingsPatch())
	}
	kust.ConfigMapGenerator = append(kust.ConfigMapGenerator, configMapEntry{
		Name:  "canasta-config",
		Files: configFiles,
	})

	// 4. Environment ConfigMap
	kust.ConfigMapGenerator = append(kust.ConfigMapGenerator, configMapEntry{
		Name: "canasta-env",
		Envs: []string{".env"},
	})

	// 5. NodePort patch for local clusters
	if localCluster {
		kust.Patches = append(kust.Patches, buildNodePortPatch())
	}

	// Marshal and write
	data, err := yaml.Marshal(&kust)
	if err != nil {
		return fmt.Errorf("failed to marshal kustomization: %w", err)
	}

	content := "# Auto-generated by Canasta CLI — do not edit manually\n" + string(data)
	return os.WriteFile(filepath.Join(installPath, "kustomization.yaml"), []byte(content), 0644)
}

// scanSettingsDir scans a directory relative to installPath and returns
// kustomize configMapGenerator file entries in key=path format.
// Skips README, .gitkeep, and .gitignore files.
func scanSettingsDir(installPath, relDir string) ([]string, error) {
	absDir := filepath.Join(installPath, relDir)
	entries, err := os.ReadDir(absDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	var files []string
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if name == "README" || name == ".gitkeep" || name == ".gitignore" {
			continue
		}
		// key=path format: the key becomes the filename inside the ConfigMap
		files = append(files, name+"="+filepath.Join(relDir, name))
	}
	sort.Strings(files)
	return files, nil
}

// buildWikiSettingsPatch returns a strategic merge patch that adds a volume
// and volumeMount for a per-wiki settings ConfigMap.
func buildWikiSettingsPatch(normalizedID, configMapName string) kustomizePatch {
	patch := fmt.Sprintf(`apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  template:
    spec:
      containers:
      - name: web
        volumeMounts:
        - mountPath: /mediawiki/config/settings/wikis/%s
          name: %s
      volumes:
      - name: %s
        configMap:
          name: %s
`, normalizedID, configMapName, configMapName, configMapName)
	return kustomizePatch{Patch: patch}
}

// buildLocalSettingsPatch returns a strategic merge patch that adds a
// subPath volumeMount for config/LocalSettings.php.
func buildLocalSettingsPatch() kustomizePatch {
	patch := `apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  template:
    spec:
      containers:
      - name: web
        volumeMounts:
        - mountPath: /mediawiki/config/LocalSettings.php
          name: canasta-config
          subPath: LocalSettings.php
`
	return kustomizePatch{Patch: patch}
}

// buildNodePortPatch returns a strategic merge patch that changes the caddy
// service to NodePort for local cluster access.
func buildNodePortPatch() kustomizePatch {
	patch := `apiVersion: v1
kind: Service
metadata:
  name: caddy-lb
spec:
  type: NodePort
  ports:
  - name: http-caddy
    port: 80
    targetPort: 80
    nodePort: 30080
  - name: https-caddy
    port: 443
    targetPort: 443
    nodePort: 30443
`
	return kustomizePatch{Patch: patch}
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
