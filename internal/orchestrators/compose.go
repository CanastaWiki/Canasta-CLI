package orchestrators

import (
	"bytes"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators/compose"
)

const (
	devComposeFile      = "docker-compose.dev.yml"
	mainComposeFile     = "docker-compose.yml"
	overrideComposeFile = "docker-compose.override.yml"
)

// ComposeOrchestrator implements Orchestrator using Docker Compose.
type ComposeOrchestrator struct{}

func (c *ComposeOrchestrator) Name() string              { return "Docker Compose" }
func (c *ComposeOrchestrator) SupportsDevMode() bool     { return true }
func (c *ComposeOrchestrator) SupportsImagePull() bool   { return true }

// getCompose returns the configured compose orchestrator path.
func (c *ComposeOrchestrator) getCompose() (config.Orchestrator, error) {
	return config.GetOrchestrator("compose")
}

func (c *ComposeOrchestrator) CheckDependencies() error {
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	if compose.Path != "" {
		cmd := exec.Command(compose.Path, "version")
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("unable to execute compose (%s)", err)
		}
	} else {
		cmd := exec.Command("docker", "compose", "version")
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("docker compose should be installed! (%s)", err)
		}
	}
	return nil
}

// WriteStackFiles writes embedded Docker Compose files to the installation directory.
// Files are only written if they don't already exist (no-clobber).
func (c *ComposeOrchestrator) WriteStackFiles(installPath string) error {
	return c.walkStackFiles(installPath, false)
}

// UpdateStackFiles compares embedded Docker Compose files with on-disk versions
// and overwrites any that differ. Returns true if anything changed.
func (c *ComposeOrchestrator) UpdateStackFiles(installPath string, dryRun bool) (bool, error) {
	changed := false
	err := fs.WalkDir(compose.StackFiles, "files", func(path string, d fs.DirEntry, err error) error {
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
			return os.MkdirAll(targetPath, permissions.DirectoryPermission)
		}
		if d.Name() == ".gitkeep" {
			return nil
		}
		embedded, err := compose.StackFiles.ReadFile(path)
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
		return os.WriteFile(targetPath, embedded, permissions.FilePermission)
	})
	return changed, err
}

// walkStackFiles walks the embedded stack files and writes them to installPath.
// If noClobber is true (create mode), existing files are skipped.
func (c *ComposeOrchestrator) walkStackFiles(installPath string, overwrite bool) error {
	return fs.WalkDir(compose.StackFiles, "files", func(path string, d fs.DirEntry, err error) error {
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
			return os.MkdirAll(targetPath, permissions.DirectoryPermission)
		}
		if d.Name() == ".gitkeep" {
			return nil
		}
		if !overwrite {
			if _, err := os.Stat(targetPath); err == nil {
				return nil // no-clobber
			}
		}
		data, err := compose.StackFiles.ReadFile(path)
		if err != nil {
			return fmt.Errorf("failed to read embedded file %s: %w", path, err)
		}
		return os.WriteFile(targetPath, data, permissions.FilePermission)
	})
}

// GetDevFiles returns the list of compose files needed for dev mode.
// It checks if docker-compose.override.yml exists and includes it.
func (c *ComposeOrchestrator) GetDevFiles(installPath string) []string {
	files := []string{mainComposeFile}

	// Include override file if it exists (contains port mappings)
	overridePath := filepath.Join(installPath, overrideComposeFile)
	if _, err := os.Stat(overridePath); err == nil {
		files = append(files, overrideComposeFile)
	}

	// Dev compose file goes last to override settings
	files = append(files, devComposeFile)
	return files
}

func (c *ComposeOrchestrator) Start(instance config.Installation) error {
	// Sync COMPOSE_PROFILES before starting so that toggling feature flags
	// in .env (e.g. CANASTA_ENABLE_ELASTICSEARCH) takes effect on restart.
	if err := syncComposeProfiles(instance.Path); err != nil {
		return err
	}

	var files []string
	if instance.DevMode {
		fmt.Println("Dev mode enabled (Xdebug active)")
		files = c.GetDevFiles(instance.Path)
	} else {
		logging.Print("Starting Canasta\n")
	}

	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	var args []string
	for _, f := range files {
		args = append(args, "-f", f)
	}
	args = append(args, "up", "-d")
	if compose.Path != "" {
		err, output := execute.Run(instance.Path, compose.Path, args...)
		if err != nil {
			return fmt.Errorf("failed to start containers at %s: %s", instance.Path, output)
		}
	} else {
		allArgs := append([]string{"compose"}, args...)
		err, output := execute.Run(instance.Path, "docker", allArgs...)
		if err != nil {
			return fmt.Errorf("failed to start containers at %s: %s", instance.Path, output)
		}
	}
	return nil
}

func (c *ComposeOrchestrator) Stop(instance config.Installation) error {
	var files []string
	if instance.DevMode {
		logging.Print("Stopping Canasta (dev mode)\n")
		files = c.GetDevFiles(instance.Path)
	} else {
		logging.Print("Stopping the containers\n")
	}

	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	var args []string
	for _, f := range files {
		args = append(args, "-f", f)
	}
	args = append(args, "down")
	if compose.Path != "" {
		err, output := execute.Run(instance.Path, compose.Path, args...)
		if err != nil {
			return fmt.Errorf("failed to stop containers at %s: %s", instance.Path, output)
		}
	} else {
		allArgs := append([]string{"compose"}, args...)
		err, output := execute.Run(instance.Path, "docker", allArgs...)
		if err != nil {
			return fmt.Errorf("failed to stop containers at %s: %s", instance.Path, output)
		}
	}
	return nil
}

func (c *ComposeOrchestrator) Pull(installPath string) error {
	logging.Print("Pulling Canasta image\n")
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	if compose.Path != "" {
		err, output := execute.Run(installPath, compose.Path, "pull", "--ignore-buildable", "--ignore-pull-failures")
		if err != nil {
			return fmt.Errorf("failed to pull images at %s: %s", installPath, output)
		}
	} else {
		err, output := execute.Run(installPath, "docker", "compose", "pull", "--ignore-buildable", "--ignore-pull-failures")
		if err != nil {
			return fmt.Errorf("failed to pull images at %s: %s", installPath, output)
		}
	}
	return nil
}

func (c *ComposeOrchestrator) Update(installPath string) (*UpdateReport, error) {
	report := &UpdateReport{}
	compose, err := c.getCompose()
	if err != nil {
		return nil, err
	}

	// Get image info before pull
	beforeImages, err := getComposeImages(installPath, compose)
	if err != nil {
		return nil, fmt.Errorf("failed to get images before pull: %w", err)
	}

	// Run pull
	if compose.Path != "" {
		err, output := execute.Run(installPath, compose.Path, "pull", "--ignore-buildable", "--ignore-pull-failures")
		if err != nil {
			return nil, fmt.Errorf("failed to pull images for update at %s: %s", installPath, output)
		}
	} else {
		err, output := execute.Run(installPath, "docker", "compose", "pull", "--ignore-buildable", "--ignore-pull-failures")
		if err != nil {
			return nil, fmt.Errorf("failed to pull images for update at %s: %s", installPath, output)
		}
	}

	// Get image info after pull
	afterImages, err := getComposeImages(installPath, compose)
	if err != nil {
		return nil, fmt.Errorf("failed to get images after pull: %w", err)
	}

	// Compare before and after
	for service, after := range afterImages {
		before, existed := beforeImages[service]
		if !existed {
			report.UpdatedImages = append(report.UpdatedImages, after)
		} else if before.ID != after.ID {
			report.UpdatedImages = append(report.UpdatedImages, after)
		} else {
			report.UnchangedImages = append(report.UnchangedImages, after)
		}
	}

	return report, nil
}

func (c *ComposeOrchestrator) Build(installPath string, files ...string) error {
	logging.Print("Building images\n")
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	var args []string
	for _, f := range files {
		args = append(args, "-f", f)
	}
	args = append(args, "build")
	if compose.Path != "" {
		err, output := execute.Run(installPath, compose.Path, args...)
		if err != nil {
			return fmt.Errorf("failed to build images at %s: %s", installPath, output)
		}
	} else {
		allArgs := append([]string{"compose"}, args...)
		err, output := execute.Run(installPath, "docker", allArgs...)
		if err != nil {
			return fmt.Errorf("failed to build images at %s: %s", installPath, output)
		}
	}
	return nil
}

func (c *ComposeOrchestrator) Destroy(installPath string) (string, error) {
	compose, err := c.getCompose()
	if err != nil {
		return "", err
	}
	var output string
	if compose.Path != "" {
		err, output = execute.Run(installPath, compose.Path, "down", "-v")
	} else {
		err, output = execute.Run(installPath, "docker", "compose", "down", "-v")
	}
	if err != nil {
		return output, err
	}

	// Remove the backup staging volume (not part of docker-compose.yml)
	execute.Run("", "docker", "volume", "rm", backupVolumeName(installPath))

	return output, nil
}

func (c *ComposeOrchestrator) ListServices(instance config.Installation) ([]string, error) {
	compose, err := c.getCompose()
	if err != nil {
		return nil, err
	}
	var cmd *exec.Cmd
	if compose.Path != "" {
		cmd = exec.Command(compose.Path, "ps", "--services")
	} else {
		cmd = exec.Command("docker", "compose", "ps", "--services")
	}
	cmd.Dir = instance.Path
	outputByte, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("failed to list services: %s", strings.TrimSpace(string(outputByte)))
	}
	var services []string
	for _, line := range strings.Split(strings.TrimSpace(string(outputByte)), "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			services = append(services, line)
		}
	}
	return services, nil
}

func (c *ComposeOrchestrator) ExecInteractive(instance config.Installation, service string, command []string) error {
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	var args []string
	if compose.Path != "" {
		args = append(args, "exec", service)
		args = append(args, command...)
		cmd := exec.Command(compose.Path, args...)
		cmd.Dir = instance.Path
		cmd.Stdin = os.Stdin
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		return cmd.Run()
	}
	args = append([]string{"compose", "exec", service}, command...)
	cmd := exec.Command("docker", args...)
	cmd.Dir = instance.Path
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func (c *ComposeOrchestrator) ExecWithError(installPath, service, command string) (string, error) {
	compose, err := c.getCompose()
	if err != nil {
		return "", err
	}
	var cmd *exec.Cmd
	if compose.Path != "" {
		cmd = exec.Command(compose.Path, "exec", "-T", service, "/bin/bash", "-c", command)
	} else {
		cmd = exec.Command("docker", "compose", "exec", "-T", service, "/bin/bash", "-c", command)
	}
	if installPath != "" {
		cmd.Dir = installPath
	}
	outputByte, err := cmd.CombinedOutput()
	output := string(outputByte)
	logging.Print(output)
	return output, err
}

func (c *ComposeOrchestrator) ExecStreaming(installPath, service, command string) error {
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	var cmd *exec.Cmd
	if compose.Path != "" {
		cmd = exec.Command(compose.Path, "exec", "-T", service, "/bin/bash", "-c", command)
	} else {
		cmd = exec.Command("docker", "compose", "exec", "-T", service, "/bin/bash", "-c", command)
	}
	if installPath != "" {
		cmd.Dir = installPath
	}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("command failed: %w", err)
	}
	return nil
}

func (c *ComposeOrchestrator) CheckRunningStatus(instance config.Installation) error {
	containerName := "web"
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	var output string
	if compose.Path != "" {
		err, output = execute.Run(instance.Path, compose.Path, "ps", "-q", containerName)
	} else {
		err, output = execute.Run(instance.Path, "docker", "compose", "ps", "-q", containerName)
	}
	if err != nil || output == "" {
		return fmt.Errorf("Container %s is not running", containerName)
	}
	return nil
}

func (c *ComposeOrchestrator) CopyFrom(installPath, service, containerPath, hostPath string) error {
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	var cmd *exec.Cmd
	if compose.Path != "" {
		cmd = exec.Command(compose.Path, "cp", service+":"+containerPath, hostPath)
	} else {
		cmd = exec.Command("docker", "compose", "cp", service+":"+containerPath, hostPath)
	}
	if installPath != "" {
		cmd.Dir = installPath
	}
	outputByte, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to copy from container: %s - %w", string(outputByte), err)
	}
	return nil
}

func (c *ComposeOrchestrator) CopyTo(installPath, service, hostPath, containerPath string) error {
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	var cmd *exec.Cmd
	if compose.Path != "" {
		cmd = exec.Command(compose.Path, "cp", hostPath, service+":"+containerPath)
	} else {
		cmd = exec.Command("docker", "compose", "cp", hostPath, service+":"+containerPath)
	}
	if installPath != "" {
		cmd.Dir = installPath
	}
	outputByte, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to copy to container: %s - %w", string(outputByte), err)
	}
	return nil
}

func (c *ComposeOrchestrator) RunBackup(installPath, envPath string, volumes map[string]string, args ...string) (string, error) {
	volName := backupVolumeName(installPath)

	// Ensure the persistent backup volume exists (idempotent)
	err, output := execute.Run("", "docker", "volume", "create", volName)
	if err != nil {
		return "", fmt.Errorf("failed to create backup volume: %s", output)
	}

	// If host paths are provided, stage them into the volume via alpine
	if len(volumes) > 0 {
		if err := stageToVolume(volName, volumes); err != nil {
			return "", err
		}
	}

	// For local repos, add a bind-mount so restic can access the host path
	if repo := repoFromArgs(args); repo != "" && isLocalRepo(repo) {
		return runResticDockerWithBindMount(installPath, envPath, volName, repo, args...)
	}

	return runResticDocker(installPath, envPath, volName, args...)
}

// runResticDockerWithBindMount runs restic with an extra bind-mount for a
// local repository path. This is Compose-specific since K8s rejects local repos.
func runResticDockerWithBindMount(installPath, envPath, volName, repoPath string, args ...string) (string, error) {
	cmdArgs := []string{"docker", "run", "--rm", "-i", "--env-file", envPath,
		"-v", volName + ":/currentsnapshot",
		"-v", repoPath + ":" + repoPath,
	}

	cmdArgs = append(cmdArgs, "restic/restic")
	cmdArgs = append(cmdArgs, args...)

	err, output := execute.Run(installPath, cmdArgs[0], cmdArgs[1:]...)
	if err != nil {
		if strings.Contains(output, "repository does not exist") {
			return output, fmt.Errorf("backup repository not found. Run 'canasta backup init' to create it")
		}
		return output, fmt.Errorf("restic command failed: %s", output)
	}
	return output, nil
}

func (c *ComposeOrchestrator) RestoreFromBackupVolume(installPath string, dirs map[string]string) error {
	return restoreFromVolume(backupVolumeName(installPath), installPath, dirs)
}

func (c *ComposeOrchestrator) CopyOverrideFile(installPath, sourceFilename, workingDir string) error {
	if sourceFilename != "" {
		logging.Print("Copying override file\n")
		if !filepath.IsAbs(sourceFilename) {
			sourceFilename = filepath.Join(workingDir, sourceFilename)
		}
		var overrideFilename = filepath.Join(installPath, "docker-compose.override.yml")
		logging.Print(fmt.Sprintf("Copying %s to %s\n", sourceFilename, overrideFilename))
		err, output := execute.Run("", "cp", sourceFilename, overrideFilename)
		if err != nil {
			return fmt.Errorf("failed to copy override file from %s: %s", sourceFilename, output)
		}
	}
	return nil
}

// getComposeImages returns a map of service name to ImageInfo
func getComposeImages(installPath string, compose config.Orchestrator) (map[string]ImageInfo, error) {
	images := make(map[string]ImageInfo)

	var output string
	var err error
	if compose.Path != "" {
		err, output = execute.Run(installPath, compose.Path, "images")
	} else {
		err, output = execute.Run(installPath, "docker", "compose", "images")
	}
	if err != nil {
		return nil, fmt.Errorf("failed to run docker compose images: %s", output)
	}

	lines := strings.Split(strings.TrimSpace(output), "\n")
	headerFound := false
	for _, line := range lines {
		if line == "" || strings.HasPrefix(line, "time=") || strings.Contains(line, "level=") {
			continue
		}

		if strings.HasPrefix(line, "CONTAINER") {
			headerFound = true
			continue
		}

		if !headerFound {
			continue
		}

		fields := strings.Fields(line)
		if len(fields) >= 4 {
			containerName := fields[0]
			parts := strings.Split(containerName, "-")
			var service string
			if len(parts) >= 3 {
				service = strings.Join(parts[1:len(parts)-1], "-")
			} else if len(parts) == 2 {
				service = parts[0]
			} else {
				parts = strings.Split(containerName, "_")
				if len(parts) >= 3 {
					service = strings.Join(parts[1:len(parts)-1], "_")
				} else if len(parts) == 2 {
					service = parts[0]
				} else {
					service = containerName
				}
			}

			if service == "" {
				continue
			}

			imageRepo := fields[1]
			imageTag := fields[2]
			imageID := fields[3]

			images[service] = ImageInfo{
				Service: service,
				Image:   imageRepo + ":" + imageTag,
				ID:      imageID,
			}
		}
	}

	return images, nil
}

// syncComposeProfiles ensures COMPOSE_PROFILES matches the feature flags in
// .env. Profiles are added when their flag is enabled and removed when
// disabled. Docker Compose requires the profile variable to activate
// optional services.
func syncComposeProfiles(installPath string) error {
	envPath := filepath.Join(installPath, ".env")
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return err
	}

	profiles := envVars["COMPOSE_PROFILES"]

	// Managed profiles: map profile name → whether it should be present
	managed := map[string]bool{
		"observable":    canasta.IsObservabilityEnabled(envVars),
		"elasticsearch": canasta.IsElasticsearchEnabled(envVars),
	}

	// Build the new profile list: keep unmanaged profiles as-is, then
	// add/remove managed ones based on the flag state.
	var kept []string
	for _, p := range strings.Split(profiles, ",") {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		if _, isManaged := managed[p]; isManaged {
			continue // will be re-added below if enabled
		}
		kept = append(kept, p)
	}
	for _, name := range []string{"observable", "elasticsearch"} {
		if managed[name] {
			kept = append(kept, name)
		}
	}

	newProfiles := strings.Join(kept, ",")
	if newProfiles == profiles {
		return nil
	}
	return canasta.SaveEnvVariable(envPath, "COMPOSE_PROFILES", newProfiles)
}

// InitConfig sets up Compose-specific configuration for a new installation.
func (c *ComposeOrchestrator) InitConfig(installPath string) error {
	if err := canasta.CreateCaddyfileSite(installPath); err != nil {
		return err
	}
	if err := canasta.CreateCaddyfileGlobal(installPath); err != nil {
		return err
	}
	if err := syncComposeProfiles(installPath); err != nil {
		return err
	}
	if _, err := canasta.EnsureObservabilityCredentials(installPath); err != nil {
		return err
	}
	return canasta.RewriteCaddy(installPath)
}

// UpdateConfig regenerates Compose-specific configuration after wikis.yaml changes.
func (c *ComposeOrchestrator) UpdateConfig(installPath string) error {
	return canasta.RewriteCaddy(installPath)
}

// MigrateConfig applies Compose-specific migration steps during upgrade.
func (c *ComposeOrchestrator) MigrateConfig(installPath string, dryRun bool) (bool, error) {
	changed := false

	caddyChanged, err := c.migrateCaddyFiles(installPath, dryRun)
	if err != nil {
		return false, err
	}
	if caddyChanged {
		changed = true
	}

	obsChanged, err := c.migrateObservability(installPath, dryRun)
	if err != nil {
		return false, err
	}
	if obsChanged {
		changed = true
	}

	return changed, nil
}

// migrateCaddyFiles creates Caddyfile.site and Caddyfile.global if they don't exist
// and rewrites the Caddyfile to include the import directives.
func (c *ComposeOrchestrator) migrateCaddyFiles(installPath string, dryRun bool) (bool, error) {
	customPath := filepath.Join(installPath, "config", "Caddyfile.site")
	globalPath := filepath.Join(installPath, "config", "Caddyfile.global")

	_, customErr := os.Stat(customPath)
	_, globalErr := os.Stat(globalPath)
	if customErr == nil && globalErr == nil {
		return false, nil
	}

	if dryRun {
		if customErr != nil {
			fmt.Println("  Would create config/Caddyfile.site")
		}
		if globalErr != nil {
			fmt.Println("  Would create config/Caddyfile.global")
		}
		fmt.Println("  Would update config/Caddyfile with import directives")
	} else {
		if customErr != nil {
			fmt.Println("  Creating config/Caddyfile.site")
			if err := canasta.CreateCaddyfileSite(installPath); err != nil {
				return false, fmt.Errorf("failed to create Caddyfile.site: %w", err)
			}
		}
		if globalErr != nil {
			fmt.Println("  Creating config/Caddyfile.global")
			if err := canasta.CreateCaddyfileGlobal(installPath); err != nil {
				return false, fmt.Errorf("failed to create Caddyfile.global: %w", err)
			}
		}
		fmt.Println("  Updating config/Caddyfile with import directives")
		if err := canasta.RewriteCaddy(installPath); err != nil {
			return false, fmt.Errorf("failed to rewrite Caddyfile: %w", err)
		}
	}

	return true, nil
}

// migrateObservability migrates old COMPOSE_PROFILES=observable to
// CANASTA_ENABLE_OBSERVABILITY=true and ensures observability credentials
// are set when observability is enabled. During dry-run it only reports
// what would change.
func (c *ComposeOrchestrator) migrateObservability(installPath string, dryRun bool) (bool, error) {
	envPath := filepath.Join(installPath, ".env")
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return false, err
	}

	changed := false

	// Migrate: if COMPOSE_PROFILES contains "observable" but
	// CANASTA_ENABLE_OBSERVABILITY is not set, add the new variable.
	hasOldProfile := canasta.ContainsProfile(envVars["COMPOSE_PROFILES"], "observable")
	hasNewVar := canasta.IsObservabilityEnabled(envVars)
	if hasOldProfile && !hasNewVar {
		if dryRun {
			fmt.Println("  Would add CANASTA_ENABLE_OBSERVABILITY=true to .env")
		} else {
			fmt.Println("  Adding CANASTA_ENABLE_OBSERVABILITY=true to .env")
			if err := canasta.SaveEnvVariable(envPath, "CANASTA_ENABLE_OBSERVABILITY", "true"); err != nil {
				return false, fmt.Errorf("failed to save CANASTA_ENABLE_OBSERVABILITY: %w", err)
			}
		}
		changed = true
		hasNewVar = true
	}

	if !hasNewVar {
		return changed, nil
	}

	// Sync COMPOSE_PROFILES for Docker Compose
	if !hasOldProfile {
		if dryRun {
			fmt.Println("  Would add observable to COMPOSE_PROFILES in .env")
		} else {
			if err := syncComposeProfiles(installPath); err != nil {
				return false, fmt.Errorf("failed to sync COMPOSE_PROFILES: %w", err)
			}
		}
		changed = true
	}

	// Check if credentials are already complete
	if envVars["OS_USER"] != "" && envVars["OS_PASSWORD"] != "" && envVars["OS_PASSWORD_HASH"] != "" {
		return changed, nil
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
