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
	"github.com/CanastaWiki/Canasta-CLI/internal/perms"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
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
	// ManagedCluster indicates the CLI created and manages the Kubernetes
	// cluster (currently via kind). Enables NodePort exposure and skips
	// the cluster-info connectivity check during create.
	ManagedCluster bool
}

func (k *KubernetesOrchestrator) Name() string              { return "Kubernetes" }
func (k *KubernetesOrchestrator) SupportsDevMode() bool     { return false }
func (k *KubernetesOrchestrator) SupportsImagePull() bool   { return false }

func (k *KubernetesOrchestrator) CheckDependencies() error {
	if _, err := exec.LookPath("kubectl"); err != nil {
		return fmt.Errorf("kubectl must be installed and in PATH")
	}
	if k.ManagedCluster {
		// For managed clusters, also require kind. Skip cluster-info check
		// because the cluster doesn't exist yet during create.
		if _, err := exec.LookPath("kind"); err != nil {
			return fmt.Errorf("kind must be installed and in PATH (see https://kind.sigs.k8s.io/)")
		}
		return nil
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
			return os.MkdirAll(targetPath, perms.DirPerm)
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
		return os.WriteFile(targetPath, embedded, perms.FilePerm)
	})
	return changed, err
}

func (k *KubernetesOrchestrator) Start(instance config.Installation) error {
	// For kind-managed clusters, ensure the cluster exists (recreate if
	// manually deleted) and set the kubectl context before applying.
	if instance.KindCluster != "" {
		httpPort, httpsPort := GetPortsFromEnv(instance.Path)
		if _, err := EnsureKindCluster(instance.KindCluster, httpPort, httpsPort); err != nil {
			return fmt.Errorf("failed to ensure kind cluster: %w", err)
		}
	}

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
	logging.Print("Waiting for web deployment to be ready (this may take a few minutes if images need to be pulled)...\n")
	rolloutCmd := exec.Command("kubectl", "rollout", "status",
		webDeployment, "-n", ns, "--timeout=600s")
	rolloutOutput, err := rolloutCmd.CombinedOutput()
	logging.Print(string(rolloutOutput))
	if err != nil {
		return fmt.Errorf("web deployment rollout failed: %s", strings.TrimSpace(string(rolloutOutput)))
	}
	return nil
}

func (k *KubernetesOrchestrator) Stop(instance config.Installation) error {
	if instance.KindCluster != "" {
		if err := setKindKubectlContext(instance.KindCluster); err != nil {
			return err
		}
	}

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
	if err := ensureKindContext(installPath); err != nil {
		return nil, err
	}

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
		webDeployment, "-n", ns, "--timeout=600s")
	rolloutOutput, err := rolloutCmd.CombinedOutput()
	logging.Print(string(rolloutOutput))
	if err != nil {
		return nil, fmt.Errorf("rollout status failed: %s", strings.TrimSpace(string(rolloutOutput)))
	}

	return &UpdateReport{}, nil
}

func (k *KubernetesOrchestrator) Destroy(installPath string) (string, error) {
	// If this is a kind-managed installation, delete the entire cluster
	// (which also removes the namespace and all resources).
	if id, err := config.GetCanastaID(installPath); err == nil {
		if inst, err := config.GetDetails(id); err == nil && inst.KindCluster != "" {
			if err := DeleteKindCluster(inst.KindCluster); err != nil {
				return "", fmt.Errorf("failed to delete kind cluster: %w", err)
			}
			return "kind cluster deleted", nil
		}
	}

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

func (k *KubernetesOrchestrator) ListServices(instance config.Installation) ([]string, error) {
	if err := ensureKindContext(instance.Path); err != nil {
		return nil, err
	}

	ns, err := getNamespaceFromPath(instance.Path)
	if err != nil {
		return nil, err
	}

	cmd := exec.Command("kubectl", "get", "pods", "-n", ns,
		"-o", fmt.Sprintf("jsonpath={range .items[*]}{.metadata.labels.%s}{\"\\n\"}{end}", podLabelKey))
	outputByte, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("failed to list services: %s", strings.TrimSpace(string(outputByte)))
	}

	seen := make(map[string]bool)
	var services []string
	for _, line := range strings.Split(strings.TrimSpace(string(outputByte)), "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !seen[line] {
			seen[line] = true
			services = append(services, line)
		}
	}
	return services, nil
}

func (k *KubernetesOrchestrator) ExecInteractive(instance config.Installation, service string, command []string) error {
	if err := ensureKindContext(instance.Path); err != nil {
		return err
	}

	ns, err := getNamespaceFromPath(instance.Path)
	if err != nil {
		return err
	}

	pod, err := getRunningPod(ns, service)
	if err != nil {
		return err
	}

	args := []string{"exec", "-it", pod, "-n", ns, "--"}
	args = append(args, command...)
	cmd := exec.Command("kubectl", args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func (k *KubernetesOrchestrator) ExecWithError(installPath, service, command string) (string, error) {
	if err := ensureKindContext(installPath); err != nil {
		return "", err
	}

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
	if err := ensureKindContext(installPath); err != nil {
		return err
	}

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
	// For kind-managed clusters, ensure the cluster exists and set context.
	if instance.KindCluster != "" {
		exists, err := kindClusterExists(instance.KindCluster)
		if err != nil {
			return err
		}
		if !exists {
			return fmt.Errorf("kind cluster %q does not exist", instance.KindCluster)
		}
		if err := setKindKubectlContext(instance.KindCluster); err != nil {
			return err
		}
	}

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
	if err := ensureKindContext(installPath); err != nil {
		return err
	}

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
	if err := ensureKindContext(installPath); err != nil {
		return err
	}

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
	if err := ensureKindContext(installPath); err != nil {
		return "", err
	}

	// Reject local repositories — K8s backups require a remote backend
	if repo := repoFromArgs(args); repo != "" && isLocalRepo(repo) {
		return "", fmt.Errorf("local backup repositories are not supported for Kubernetes; use a remote repository (s3:, sftp:, rest:, etc.)")
	}

	volName := backupVolumeName(installPath)

	// Docker is available (required by kind), so create the staging volume
	err, output := execute.Run("", "docker", "volume", "create", volName)
	if err != nil {
		return "", fmt.Errorf("failed to create backup volume: %s", output)
	}

	if len(volumes) > 0 {
		// Sync cluster-side data (extensions, skins, images, database dumps)
		// to the CLI host before staging into the Docker volume
		if err := k.syncClusterDataToHost(installPath); err != nil {
			return "", fmt.Errorf("failed to sync cluster data to host: %w", err)
		}
		if err := stageToVolume(volName, volumes); err != nil {
			return "", err
		}
	}

	return runResticDocker(installPath, envPath, volName, args...)
}

func (k *KubernetesOrchestrator) RestoreFromBackupVolume(installPath string, dirs map[string]string) error {
	if err := ensureKindContext(installPath); err != nil {
		return err
	}

	volName := backupVolumeName(installPath)

	// Copy from Docker volume to CLI host (same logic as Compose)
	if err := restoreFromVolume(volName, installPath, dirs); err != nil {
		return err
	}

	// Sync cluster-side data (extensions, skins, images) from CLI host to cluster
	return k.syncHostDataToCluster(installPath)
}

// syncClusterDataToHost copies cluster-side data from the web pod to the CLI
// host so it can be staged into the Docker backup volume. This includes:
// - Database dumps from /mediawiki/config/backup/
// - User extensions from /var/www/mediawiki/w/user-extensions/
// - User skins from /var/www/mediawiki/w/user-skins/
// - Images from /mediawiki/images/
//
// Uses tar to copy directory CONTENTS (not the directory itself) to avoid
// nesting issues with kubectl cp when the target directory already exists.
func (k *KubernetesOrchestrator) syncClusterDataToHost(installPath string) error {
	syncs := []struct {
		containerDir string
		hostDir      string
	}{
		{"/mediawiki/config/backup", filepath.Join(installPath, "config", "backup")},
		{"/var/www/mediawiki/w/user-extensions", filepath.Join(installPath, "extensions")},
		{"/var/www/mediawiki/w/user-skins", filepath.Join(installPath, "skins")},
		{"/mediawiki/images", filepath.Join(installPath, "images")},
	}

	for _, s := range syncs {
		if err := os.MkdirAll(s.hostDir, perms.DirPerm); err != nil {
			return fmt.Errorf("failed to create directory %s: %w", s.hostDir, err)
		}
		if err := k.copyDirContentsFrom(installPath, "web", s.containerDir, s.hostDir); err != nil {
			return fmt.Errorf("failed to copy %s from cluster: %w", s.containerDir, err)
		}
	}
	return nil
}

// syncHostDataToCluster copies restored data from the CLI host back to the
// web pod in the cluster. Config files (.env, wikis.yaml, Caddyfile, settings/)
// stay on the CLI host and are applied via ConfigMaps on the next kustomize apply.
//
// Uses tar to copy directory CONTENTS to avoid nesting issues with kubectl cp.
func (k *KubernetesOrchestrator) syncHostDataToCluster(installPath string) error {
	syncs := []struct {
		hostDir      string
		containerDir string
	}{
		{filepath.Join(installPath, "extensions"), "/var/www/mediawiki/w/user-extensions"},
		{filepath.Join(installPath, "skins"), "/var/www/mediawiki/w/user-skins"},
		{filepath.Join(installPath, "images"), "/mediawiki/images"},
	}

	for _, s := range syncs {
		if _, err := os.Stat(s.hostDir); os.IsNotExist(err) {
			continue
		}
		if err := k.copyDirContentsTo(installPath, "web", s.hostDir, s.containerDir); err != nil {
			return fmt.Errorf("failed to copy %s to cluster: %w", s.hostDir, err)
		}
	}
	return nil
}

// copyDirContentsFrom copies the CONTENTS of a container directory to a host
// directory using tar, avoiding the nesting problem where kubectl cp copies
// the directory itself into the target.
func (k *KubernetesOrchestrator) copyDirContentsFrom(installPath, service, containerDir, hostDir string) error {
	if err := ensureKindContext(installPath); err != nil {
		return err
	}

	ns, err := getNamespaceFromPath(installPath)
	if err != nil {
		return err
	}

	pod, err := getRunningPod(ns, service)
	if err != nil {
		return err
	}

	// kubectl exec pod -- tar cf - -C /container/dir . | tar xf - -C /host/dir
	tarCmd := exec.Command("kubectl", "exec", pod, "-n", ns,
		"--", "tar", "cf", "-", "-C", containerDir, ".")
	untarCmd := exec.Command("tar", "xf", "-", "-C", hostDir)

	pipe, err := tarCmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("failed to create pipe: %w", err)
	}
	untarCmd.Stdin = pipe

	if err := untarCmd.Start(); err != nil {
		return fmt.Errorf("failed to start local tar: %w", err)
	}
	if err := tarCmd.Run(); err != nil {
		return fmt.Errorf("failed to tar from container %s: %w", containerDir, err)
	}
	if err := untarCmd.Wait(); err != nil {
		return fmt.Errorf("failed to extract to %s: %w", hostDir, err)
	}
	return nil
}

// copyDirContentsTo copies the CONTENTS of a host directory to a container
// directory using tar, avoiding the nesting problem where kubectl cp copies
// the directory itself into the target.
func (k *KubernetesOrchestrator) copyDirContentsTo(installPath, service, hostDir, containerDir string) error {
	if err := ensureKindContext(installPath); err != nil {
		return err
	}

	ns, err := getNamespaceFromPath(installPath)
	if err != nil {
		return err
	}

	pod, err := getRunningPod(ns, service)
	if err != nil {
		return err
	}

	// tar cf - -C /host/dir . | kubectl exec -i pod -- tar xf - -C /container/dir
	tarCmd := exec.Command("tar", "cf", "-", "-C", hostDir, ".")
	untarCmd := exec.Command("kubectl", "exec", "-i", pod, "-n", ns,
		"--", "tar", "xf", "-", "-C", containerDir)

	pipe, err := tarCmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("failed to create pipe: %w", err)
	}
	untarCmd.Stdin = pipe

	if err := untarCmd.Start(); err != nil {
		return fmt.Errorf("failed to start kubectl tar: %w", err)
	}
	if err := tarCmd.Run(); err != nil {
		return fmt.Errorf("failed to tar from %s: %w", hostDir, err)
	}
	if err := untarCmd.Wait(); err != nil {
		return fmt.Errorf("failed to extract to container %s: %w", containerDir, err)
	}
	return nil
}

func (k *KubernetesOrchestrator) InitConfig(installPath string) error {
	if err := canasta.CreateCaddyfileSite(installPath); err != nil {
		return err
	}
	if err := canasta.CreateCaddyfileGlobal(installPath); err != nil {
		return err
	}
	if _, err := canasta.EnsureObservabilityCredentials(installPath); err != nil {
		return err
	}
	if err := canasta.RewriteCaddy(installPath); err != nil {
		return err
	}
	return k.generateKustomization(installPath, k.ManagedCluster)
}

func (k *KubernetesOrchestrator) UpdateConfig(installPath string) error {
	if err := canasta.RewriteCaddy(installPath); err != nil {
		return err
	}
	managedCluster := false
	if id, err := config.GetCanastaID(installPath); err == nil {
		if inst, err := config.GetDetails(id); err == nil {
			managedCluster = inst.ManagedCluster
		}
	}
	return k.generateKustomization(installPath, managedCluster)
}

// kustomization is the Go representation of kustomization.yaml.
type kustomization struct {
	APIVersion         string              `yaml:"apiVersion"`
	Kind               string              `yaml:"kind"`
	Namespace          string              `yaml:"namespace"`
	Resources          []string            `yaml:"resources"`
	ConfigMapGenerator []configMapEntry    `yaml:"configMapGenerator"`
	Images             []kustomizeImage    `yaml:"images,omitempty"`
	Patches            []kustomizePatch    `yaml:"patches,omitempty"`
}

// defaultCanastaImage is the image reference hardcoded in web.yaml.
const defaultCanastaImage = "ghcr.io/canastawiki/canasta"

type configMapEntry struct {
	Name  string   `yaml:"name"`
	Files []string `yaml:"files,omitempty"`
	Envs  []string `yaml:"envs,omitempty"`
}

type kustomizePatch struct {
	Patch string `yaml:"patch"`
}

type kustomizeImage struct {
	Name    string `yaml:"name"`
	NewName string `yaml:"newName,omitempty"`
	NewTag  string `yaml:"newTag,omitempty"`
}

// generateKustomization programmatically generates kustomization.yaml by
// scanning the installation directory for settings files and wikis.
func (k *KubernetesOrchestrator) generateKustomization(installPath string, managedCluster bool) error {
	namespace := filepath.Base(installPath)

	kust := kustomization{
		APIVersion: "kustomize.config.k8s.io/v1beta1",
		Kind:       "Kustomization",
		Namespace:  namespace,
		Resources: []string{
			"kubernetes/namespace.yaml",
			"kubernetes/caddy.yaml",
			"kubernetes/db.yaml",
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

	// 5. NodePort patch for managed clusters
	if managedCluster {
		kust.Patches = append(kust.Patches, buildNodePortPatch())
	}

	// 6. Observability stack (OpenSearch + Fluent Bit + Dashboards)
	envPath := filepath.Join(installPath, ".env")
	if envVars, err := canasta.GetEnvVariable(envPath); err == nil {
		if canasta.IsObservabilityEnabled(envVars) {
			kust.Resources = append(kust.Resources,
				"kubernetes/log-pvcs.yaml",
				"kubernetes/opensearch.yaml",
				"kubernetes/opensearch-dashboards.yaml",
				"kubernetes/fluent-bit-config.yaml",
				"kubernetes/fluent-bit.yaml",
				"kubernetes/observable-init.yaml",
			)
			kust.Patches = append(kust.Patches,
				buildLogVolumePatch("web", "/var/log/mediawiki", "mediawiki-logs"),
				buildLogVolumePatch("caddy", "/var/log/caddy", "caddy-logs"),
				buildLogVolumePatch("db", "/var/log/mysql", "mysql-logs"),
			)
		}
	}

	// 7. Elasticsearch (optional)
	if envVars, err := canasta.GetEnvVariable(envPath); err == nil {
		if canasta.IsElasticsearchEnabled(envVars) {
			kust.Resources = append(kust.Resources, "kubernetes/elasticsearch.yaml")
			kust.Patches = append(kust.Patches, buildElasticsearchInitPatch())
		}
	}

	// 8. Image override (for local builds pushed to a registry)
	if envVars, err := canasta.GetEnvVariable(envPath); err == nil {
		if canastaImage := envVars["CANASTA_IMAGE"]; canastaImage != "" {
			// Parse "registry/repo:tag" into newName and newTag
			newName := canastaImage
			newTag := ""
			if idx := strings.LastIndex(canastaImage, ":"); idx != -1 {
				newName = canastaImage[:idx]
				newTag = canastaImage[idx+1:]
			}
			img := kustomizeImage{Name: defaultCanastaImage, NewName: newName}
			if newTag != "" {
				img.NewTag = newTag
			}
			kust.Images = append(kust.Images, img)
		}
	}

	// Marshal and write
	data, err := yaml.Marshal(&kust)
	if err != nil {
		return fmt.Errorf("failed to marshal kustomization: %w", err)
	}

	content := "# Auto-generated by Canasta CLI — do not edit manually\n" + string(data)
	return os.WriteFile(filepath.Join(installPath, "kustomization.yaml"), []byte(content), perms.FilePerm)
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

// buildLogVolumePatch returns a strategic merge patch that adds a PVC-backed
// log volume to a deployment so a standalone Fluent Bit pod can read the logs.
func buildLogVolumePatch(deployment, logPath, pvcName string) kustomizePatch {
	patch := fmt.Sprintf(`apiVersion: apps/v1
kind: Deployment
metadata:
  name: %s
spec:
  template:
    spec:
      containers:
      - name: %s
        volumeMounts:
        - mountPath: %s
          name: %s
      volumes:
      - name: %s
        persistentVolumeClaim:
          claimName: %s
`, deployment, deployment, logPath, pvcName, pvcName, pvcName)
	return kustomizePatch{Patch: patch}
}

// buildElasticsearchInitPatch returns a strategic merge patch that adds the
// wait-for-elasticsearch init container to the web deployment.
func buildElasticsearchInitPatch() kustomizePatch {
	patch := `apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  template:
    spec:
      initContainers:
      - name: wait-for-elasticsearch
        image: busybox:1.36
        command: ['sh', '-c', 'until nc -z elasticsearch 9200; do echo "Waiting for elasticsearch..."; sleep 5; done']
`
	return kustomizePatch{Patch: patch}
}

func (k *KubernetesOrchestrator) MigrateConfig(installPath string, dryRun bool) (bool, error) {
	envPath := filepath.Join(installPath, ".env")
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return false, err
	}

	if !canasta.IsObservabilityEnabled(envVars) {
		return false, nil
	}

	// Check if credentials are already complete
	if envVars["OS_USER"] != "" && envVars["OS_PASSWORD"] != "" && envVars["OS_PASSWORD_HASH"] != "" {
		return false, nil
	}

	if dryRun {
		fmt.Println("  Would generate OpenSearch observability credentials in .env")
		return true, nil
	}

	fmt.Println("  Generating OpenSearch observability credentials")
	if _, err := canasta.EnsureObservabilityCredentials(installPath); err != nil {
		return false, fmt.Errorf("failed to ensure observability credentials: %w", err)
	}

	return true, nil
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
			return os.MkdirAll(targetPath, perms.DirPerm)
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
		return os.WriteFile(targetPath, data, perms.FilePerm)
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
