package orchestrators

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
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

func (c *ComposeOrchestrator) Name() string            { return "Docker Compose" }
func (c *ComposeOrchestrator) SupportsDevMode() bool   { return true }
func (c *ComposeOrchestrator) SupportsImagePull() bool { return true }

type composeBinary struct {
	Path string
}

// getCompose discovers the compose binary at runtime.
func (c *ComposeOrchestrator) getCompose() (composeBinary, error) {
	if path, err := exec.LookPath("docker-compose"); err == nil {
		return composeBinary{Path: path}, nil
	}
	return composeBinary{}, nil
}

// runCompose runs a compose command via execute.Run and returns the output.
func runCompose(installPath string, compose composeBinary, args ...string) (string, error) {
	if compose.Path != "" {
		return execute.Run(installPath, compose.Path, args...)
	}
	allArgs := append([]string{"compose"}, args...)
	return execute.Run(installPath, "docker", allArgs...)
}

// composeCommand returns an exec.Cmd configured for the compose orchestrator.
func composeCommand(compose composeBinary, args ...string) *exec.Cmd {
	if compose.Path != "" {
		// compose.Path is from system lookup.
		//nolint:gosec
		return exec.Command(compose.Path, args...)
	}
	allArgs := append([]string{"compose"}, args...)
	return exec.Command("docker", allArgs...)
}

func (c *ComposeOrchestrator) CheckDependencies() error {
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	cmd := composeCommand(compose, "version")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("docker compose is not available: %w", err)
	}
	return nil
}

// WriteStackFiles writes embedded Docker Compose files to the instance directory.
// Files are only written if they don't already exist (no-clobber).
func (c *ComposeOrchestrator) WriteStackFiles(installPath string) error {
	return c.walkStackFiles(installPath, false)
}

// UpdateStackFiles compares embedded Docker Compose files with on-disk versions
// and overwrites any that differ. Returns true if anything changed.
func (c *ComposeOrchestrator) UpdateStackFiles(installPath string, dryRun bool) (bool, error) {
	return updateStackFiles(compose.StackFiles, "files", installPath, dryRun)
}

func (c *ComposeOrchestrator) walkStackFiles(installPath string, overwrite bool) error {
	return writeStackFiles(compose.StackFiles, "files", installPath, overwrite)
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

func (c *ComposeOrchestrator) Start(instance config.Instance) error {
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
	output, err := runCompose(instance.Path, compose, args...)
	if err != nil {
		return fmt.Errorf("failed to start containers at %s: %s", instance.Path, output)
	}
	return nil
}

func (c *ComposeOrchestrator) Stop(instance config.Instance) error {
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
	output, err := runCompose(instance.Path, compose, args...)
	if err != nil {
		return fmt.Errorf("failed to stop containers at %s: %s", instance.Path, output)
	}
	return nil
}

func (c *ComposeOrchestrator) Pull(installPath string) error {
	logging.Print("Pulling Canasta image\n")
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	output, err := runCompose(installPath, compose, "pull", "--ignore-buildable", "--ignore-pull-failures")
	if err != nil {
		return fmt.Errorf("failed to pull images at %s: %s", installPath, output)
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
	output, err := runCompose(installPath, compose, "pull", "--ignore-buildable", "--ignore-pull-failures")
	if err != nil {
		return nil, fmt.Errorf("failed to pull images for update at %s: %s", installPath, output)
	}

	// Get image info after pull
	afterImages, err := getComposeImages(installPath, compose)
	if err != nil {
		return nil, fmt.Errorf("failed to get images after pull: %w", err)
	}

	// Compare before and after
	for service, after := range afterImages {
		before, existed := beforeImages[service]
		switch {
		case !existed:
			report.UpdatedImages = append(report.UpdatedImages, after)
		case before.ID != after.ID:
			report.UpdatedImages = append(report.UpdatedImages, after)
		default:
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
	output, err := runCompose(installPath, compose, args...)
	if err != nil {
		return fmt.Errorf("failed to build images at %s: %s", installPath, output)
	}
	return nil
}

func (c *ComposeOrchestrator) Destroy(installPath string) (string, error) {
	compose, err := c.getCompose()
	if err != nil {
		return "", err
	}
	output, err := runCompose(installPath, compose, "down", "-v")
	if err != nil {
		return output, err
	}

	// Remove the backup staging volume (not part of docker-compose.yml)
	if rmOutput, rmErr := execute.Run("", "docker", "volume", "rm", backupVolumeName(installPath)); rmErr != nil {
		logging.Print(fmt.Sprintf("Warning: failed to remove backup volume: %s\n", rmOutput))
	}

	return output, nil
}

func (c *ComposeOrchestrator) ListServices(instance config.Instance) ([]string, error) {
	compose, err := c.getCompose()
	if err != nil {
		return nil, err
	}
	cmd := composeCommand(compose, "ps", "--services")
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

func (c *ComposeOrchestrator) ExecInteractive(instance config.Instance, service string, command []string) error {
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	args := append([]string{"exec", service}, command...)
	cmd := composeCommand(compose, args...)
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
	cmd := composeCommand(compose, "exec", "-T", service, "/bin/bash", "-c", command)
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
	cmd := composeCommand(compose, "exec", "-T", service, "/bin/bash", "-c", command)
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

func (c *ComposeOrchestrator) CheckRunningStatus(instance config.Instance) error {
	containerName := "web"
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	output, err := runCompose(instance.Path, compose, "ps", "-q", containerName)
	if err != nil || output == "" {
		return fmt.Errorf("container %s is not running", containerName)
	}
	return nil
}

func (c *ComposeOrchestrator) CopyFrom(installPath, service, containerPath, hostPath string) error {
	compose, err := c.getCompose()
	if err != nil {
		return err
	}
	cmd := composeCommand(compose, "cp", service+":"+containerPath, hostPath)
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
	cmd := composeCommand(compose, "cp", hostPath, service+":"+containerPath)
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
	output, err := execute.Run("", "docker", "volume", "create", volName)
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
		"--user", currentUser(),
		"-v", volName + ":/currentsnapshot",
		"-v", repoPath + ":" + repoPath,
	}

	cmdArgs = append(cmdArgs, "restic/restic", "--cache-dir", "/tmp/restic-cache")
	cmdArgs = append(cmdArgs, args...)

	output, err := execute.Run(installPath, cmdArgs[0], cmdArgs[1:]...)
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

func (c *ComposeOrchestrator) CopyOverrideFile(installPath, sourceFilename string) error {
	if sourceFilename != "" {
		logging.Print("Copying override file\n")
		var overrideFilename = filepath.Join(installPath, "docker-compose.override.yml")
		logging.Print(fmt.Sprintf("Copying %s to %s\n", sourceFilename, overrideFilename))
		output, err := execute.Run("", "cp", sourceFilename, overrideFilename)
		if err != nil {
			return fmt.Errorf("failed to copy override file from %s: %s", sourceFilename, output)
		}
	}
	return nil
}

// composeImageEntry represents one element from "docker compose images --format json".
type composeImageEntry struct {
	ContainerName string `json:"ContainerName"`
	Repository    string `json:"Repository"`
	Tag           string `json:"Tag"`
	ID            string `json:"ID"`
}

// getComposeImages returns a map of service name to ImageInfo.
func getComposeImages(installPath string, compose composeBinary) (map[string]ImageInfo, error) {
	output, err := runCompose(installPath, compose, "images", "--format", "json")
	if err != nil {
		return nil, fmt.Errorf("failed to run docker compose images: %s", output)
	}

	var entries []composeImageEntry
	if err := json.Unmarshal([]byte(output), &entries); err != nil {
		return nil, fmt.Errorf("failed to parse docker compose images output: %w", err)
	}

	project := filepath.Base(installPath)
	images := make(map[string]ImageInfo, len(entries))
	for _, e := range entries {
		service := serviceFromContainer(e.ContainerName, project)
		images[service] = ImageInfo{
			Service: service,
			Image:   e.Repository + ":" + e.Tag,
			ID:      e.ID,
		}
	}
	return images, nil
}

// serviceFromContainer extracts the service name from a Docker Compose
// container name. Compose v2 uses "{project}-{service}-{number}".
func serviceFromContainer(containerName, project string) string {
	// Strip the project prefix and separator
	after, found := strings.CutPrefix(containerName, project+"-")
	if !found {
		// Legacy underscore separator: {project}_{service}_{number}
		after, found = strings.CutPrefix(containerName, project+"_")
		if !found {
			return containerName
		}
	}
	// Strip the trailing "-{number}" or "_{number}"
	if idx := strings.LastIndexAny(after, "-_"); idx > 0 {
		return after[:idx]
	}
	return after
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
		"varnish":       canasta.IsVarnishEnabled(envVars),
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
	for _, name := range []string{"observable", "elasticsearch", "varnish"} {
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

// InitConfig sets up Compose-specific configuration for a new instance.
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

// migrateCaddyFiles creates Caddyfile.site and Caddyfile.global if they don't exist,
// renames legacy Caddyfile.custom to Caddyfile.site, and rewrites the Caddyfile to
// use the current import directives.
func (c *ComposeOrchestrator) migrateCaddyFiles(installPath string, dryRun bool) (bool, error) {
	sitePath := filepath.Join(installPath, "config", "Caddyfile.site")
	globalPath := filepath.Join(installPath, "config", "Caddyfile.global")
	legacyCustomPath := filepath.Join(installPath, "config", "Caddyfile.custom")

	changed := false

	// Rename legacy Caddyfile.custom → Caddyfile.site if the old name exists
	if _, err := os.Stat(legacyCustomPath); err == nil {
		if dryRun {
			fmt.Println("  Would rename config/Caddyfile.custom to config/Caddyfile.site")
		} else {
			fmt.Println("  Renaming config/Caddyfile.custom to config/Caddyfile.site")
			if err := os.Rename(legacyCustomPath, sitePath); err != nil {
				return false, fmt.Errorf("failed to rename Caddyfile.custom: %w", err)
			}
		}
		changed = true
	}

	// Check whether the Caddyfile still references the old name
	caddyfilePath := filepath.Join(installPath, "config", "Caddyfile")
	if content, err := os.ReadFile(caddyfilePath); err == nil {
		if strings.Contains(string(content), "Caddyfile.custom") {
			changed = true
		}
	}

	_, siteErr := os.Stat(sitePath)
	_, globalErr := os.Stat(globalPath)
	if siteErr != nil {
		changed = true
	}
	if globalErr != nil {
		changed = true
	}

	if !changed {
		return false, nil
	}

	if dryRun {
		if siteErr != nil {
			fmt.Println("  Would create config/Caddyfile.site")
		}
		if globalErr != nil {
			fmt.Println("  Would create config/Caddyfile.global")
		}
		fmt.Println("  Would update config/Caddyfile with import directives")
	} else {
		if siteErr != nil {
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
