package orchestrators

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

const (
	devComposeFile      = "docker-compose.dev.yml"
	mainComposeFile     = "docker-compose.yml"
	overrideComposeFile = "docker-compose.override.yml"
)

// ComposeOrchestrator implements Orchestrator using Docker Compose.
type ComposeOrchestrator struct{}

// getCompose returns the configured compose orchestrator path.
func (c *ComposeOrchestrator) getCompose() config.Orchestrator {
	orch, _ := config.GetOrchestrator("compose")
	return orch
}

func (c *ComposeOrchestrator) CheckDependencies() error {
	compose := c.getCompose()
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

func (c *ComposeOrchestrator) GetRepoLink() string {
	return "https://github.com/CanastaWiki/Canasta-DockerCompose.git"
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
	var files []string
	if instance.DevMode {
		fmt.Println("Dev mode enabled (Xdebug active)")
		files = c.GetDevFiles(instance.Path)
	} else {
		logging.Print("Starting Canasta\n")
	}

	compose := c.getCompose()
	var args []string
	for _, f := range files {
		args = append(args, "-f", f)
	}
	args = append(args, "up", "-d")
	if compose.Path != "" {
		err, output := execute.Run(instance.Path, compose.Path, args...)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	} else {
		allArgs := append([]string{"compose"}, args...)
		err, output := execute.Run(instance.Path, "docker", allArgs...)
		if err != nil {
			return fmt.Errorf("%s", output)
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

	compose := c.getCompose()
	var args []string
	for _, f := range files {
		args = append(args, "-f", f)
	}
	args = append(args, "down")
	if compose.Path != "" {
		err, output := execute.Run(instance.Path, compose.Path, args...)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	} else {
		allArgs := append([]string{"compose"}, args...)
		err, output := execute.Run(instance.Path, "docker", allArgs...)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	}
	return nil
}

func (c *ComposeOrchestrator) Pull(installPath string) error {
	logging.Print("Pulling Canasta image\n")
	compose := c.getCompose()
	if compose.Path != "" {
		err, output := execute.Run(installPath, compose.Path, "pull", "--ignore-buildable", "--ignore-pull-failures")
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	} else {
		err, output := execute.Run(installPath, "docker", "compose", "pull", "--ignore-buildable", "--ignore-pull-failures")
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	}
	return nil
}

func (c *ComposeOrchestrator) Update(installPath string) (*UpdateReport, error) {
	report := &UpdateReport{}
	compose := c.getCompose()

	// Get image info before pull
	beforeImages, err := getComposeImages(installPath, compose)
	if err != nil {
		return nil, fmt.Errorf("failed to get images before pull: %w", err)
	}

	// Run pull
	if compose.Path != "" {
		err, output := execute.Run(installPath, compose.Path, "pull", "--ignore-buildable", "--ignore-pull-failures")
		if err != nil {
			return nil, fmt.Errorf("%s", output)
		}
	} else {
		err, output := execute.Run(installPath, "docker", "compose", "pull", "--ignore-buildable", "--ignore-pull-failures")
		if err != nil {
			return nil, fmt.Errorf("%s", output)
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
	compose := c.getCompose()
	var args []string
	for _, f := range files {
		args = append(args, "-f", f)
	}
	args = append(args, "build")
	if compose.Path != "" {
		err, output := execute.Run(installPath, compose.Path, args...)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	} else {
		allArgs := append([]string{"compose"}, args...)
		err, output := execute.Run(installPath, "docker", allArgs...)
		if err != nil {
			return fmt.Errorf("%s", output)
		}
	}
	return nil
}

func (c *ComposeOrchestrator) Destroy(installPath string) (string, error) {
	compose := c.getCompose()
	if compose.Path != "" {
		err, output := execute.Run(installPath, compose.Path, "down", "-v")
		return output, err
	}
	err, output := execute.Run(installPath, "docker", "compose", "down", "-v")
	return output, err
}

func (c *ComposeOrchestrator) ExecWithError(installPath, service, command string) (string, error) {
	compose := c.getCompose()
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
	compose := c.getCompose()
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
	compose := c.getCompose()
	var output string
	var err error
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
	compose := c.getCompose()
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
	compose := c.getCompose()
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

func (c *ComposeOrchestrator) CopyOverrideFile(installPath, sourceFilename, workingDir string) error {
	if sourceFilename != "" {
		logging.Print("Copying override file\n")
		if !strings.HasPrefix(sourceFilename, "/") {
			sourceFilename = workingDir + "/" + sourceFilename
		}
		var overrideFilename = installPath + "/docker-compose.override.yml"
		logging.Print(fmt.Sprintf("Copying %s to %s\n", sourceFilename, overrideFilename))
		err, output := execute.Run("", "cp", sourceFilename, overrideFilename)
		if err != nil {
			return fmt.Errorf("%s", output)
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
