package devmode

import (
	"embed"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

const (
	DevComposeFile     = "docker-compose.dev.yml"
	CodeDir            = "mediawiki-code"
	VSCodeDir          = ".vscode"
	PHPStormDir        = ".idea"
	PHPStormRunConfDir = ".idea/runConfigurations"
)

// Embed the dev mode files at compile time
//
//go:embed files/Dockerfile.xdebug
var dockerfileXdebugContent string

//go:embed files/docker-compose.dev.yml
var dockerComposeDevContent string

//go:embed files/xdebug.ini
var xdebugIniContent string

//go:embed files/vscode-launch.json
var vscodeLaunchContent string

//go:embed files/phpstorm/php.xml
var phpstormServerConfig string

//go:embed files/phpstorm/Listen_for_Xdebug.xml
var phpstormRunConfig string

// For future use if we need to embed multiple files or directories
//
//go:embed files/*
var embeddedFiles embed.FS

// CreateDevModeFiles creates all the xdebug-related files in the installation directory
func CreateDevModeFiles(installPath string) error {
	logging.Print("Creating development mode files...\n")

	// Create Dockerfile.xdebug
	dockerfilePath := filepath.Join(installPath, "Dockerfile.xdebug")
	if err := os.WriteFile(dockerfilePath, []byte(dockerfileXdebugContent), 0644); err != nil {
		return fmt.Errorf("failed to create Dockerfile.xdebug: %w", err)
	}

	// Create docker-compose.dev.yml
	devComposePath := filepath.Join(installPath, DevComposeFile)
	if err := os.WriteFile(devComposePath, []byte(dockerComposeDevContent), 0644); err != nil {
		return fmt.Errorf("failed to create docker-compose.dev.yml: %w", err)
	}

	// Create config/xdebug.ini
	xdebugIniPath := filepath.Join(installPath, "config", "xdebug.ini")
	if err := os.WriteFile(xdebugIniPath, []byte(xdebugIniContent), 0644); err != nil {
		return fmt.Errorf("failed to create xdebug.ini: %w", err)
	}

	logging.Print("Development mode files created\n")
	return nil
}

// ExtractMediaWikiCode extracts code from the web container for live editing
// Uses raw docker commands (not docker compose) to avoid bind mount validation issues
// If the code directory already exists with content, it skips extraction and returns true
func ExtractMediaWikiCode(installPath, orchestrator, imageTag string) error {
	codeDir := filepath.Join(installPath, CodeDir)

	// Check if mediawiki-code directory already exists with content
	if info, err := os.Stat(codeDir); err == nil && info.IsDir() {
		entries, err := os.ReadDir(codeDir)
		if err == nil && len(entries) > 0 {
			logging.Print(fmt.Sprintf("WARNING: %s already exists and contains files.\n", codeDir))
			logging.Print("Skipping code extraction. To regenerate, delete this directory and restart with --dev.\n")
			return nil
		}
	}

	logging.Print("Extracting MediaWiki code for live editing...\n")

	// Determine the image to use
	image := fmt.Sprintf("ghcr.io/canastawiki/canasta:%s", imageTag)

	// Pull the image first
	logging.Print(fmt.Sprintf("Pulling image %s...\n", image))
	if err, output := execute.Run("", "docker", "pull", image); err != nil {
		return fmt.Errorf("failed to pull image: %s", output)
	}

	// Create the destination directory
	if err := os.MkdirAll(codeDir, 0755); err != nil {
		return fmt.Errorf("failed to create code directory: %w", err)
	}

	// Start a container and run the symlinks script to create extension/skin symlinks
	// (normally /create-symlinks.sh runs as part of the entrypoint, but we bypass it with sleep)
	containerName := "canasta-code-extract-temp"
	logging.Print("Starting temporary container for code extraction...\n")
	if err, output := execute.Run("", "docker", "run", "-d", "--name", containerName, image, "sleep", "infinity"); err != nil {
		return fmt.Errorf("failed to start temporary container: %s", output)
	}

	// Run the symlinks script to create extensions/ and skins/ symlinks
	logging.Print("Creating extension and skin symlinks...\n")
	if err, output := execute.Run("", "docker", "exec", containerName, "/create-symlinks.sh"); err != nil {
		execute.Run("", "docker", "rm", "-f", containerName)
		return fmt.Errorf("failed to create symlinks: %s", output)
	}

	// Copy code from container to host using tar to dereference symlinks recursively
	// (docker cp -L only follows top-level symlinks, not recursive ones)
	// Extensions/skins are symlinked to canasta-extensions/canasta-skins
	// Note: Can't use execute.Run here because it wraps commands in bash -c which breaks pipes
	logging.Print(fmt.Sprintf("Copying MediaWiki code to %s...\n", codeDir))
	tarCmd := fmt.Sprintf("docker exec %s tar -chf - -C /var/www/mediawiki/w . | tar -xf - -C %s", containerName, codeDir)
	cmd := exec.Command("bash", "-c", tarCmd)
	if output, err := cmd.CombinedOutput(); err != nil {
		// Clean up container on failure
		execute.Run("", "docker", "rm", "-f", containerName)
		return fmt.Errorf("failed to copy code from container: %s (output: %s)", err, string(output))
	}

	// Remove the temporary container
	logging.Print("Removing temporary container...\n")
	if err, output := execute.Run("", "docker", "rm", "-f", containerName); err != nil {
		return fmt.Errorf("failed to remove temporary container: %s", output)
	}

	logging.Print(fmt.Sprintf("MediaWiki code extracted to %s\n", codeDir))
	return nil
}

// SetupDevEnvironment configures .env and creates IDE configs (VSCode and PHPStorm)
func SetupDevEnvironment(installPath, imageTag string) error {
	logging.Print("Setting up development environment...\n")

	// Update .env file to set DEV_CODE_PATH and CANASTA_IMAGE_TAG
	envPath := filepath.Join(installPath, ".env")
	envContent, err := os.ReadFile(envPath)
	if err != nil {
		return fmt.Errorf("failed to read .env file: %w", err)
	}

	// Append dev settings to .env
	devSettings := fmt.Sprintf("\n# Development mode settings\nDEV_CODE_PATH=./mediawiki-code\nCANASTA_IMAGE_TAG=%s\n", imageTag)
	envContent = append(envContent, []byte(devSettings)...)
	if err := os.WriteFile(envPath, envContent, 0644); err != nil {
		return fmt.Errorf("failed to update .env file: %w", err)
	}

	// Create .vscode directory and write launch.json
	vscodeDir := filepath.Join(installPath, VSCodeDir)
	if err := os.MkdirAll(vscodeDir, 0755); err != nil {
		return fmt.Errorf("failed to create .vscode directory: %w", err)
	}

	// Write VSCode launch.json
	launchJsonPath := filepath.Join(vscodeDir, "launch.json")
	if err := os.WriteFile(launchJsonPath, []byte(vscodeLaunchContent), 0644); err != nil {
		return fmt.Errorf("failed to create VSCode launch.json: %w", err)
	}

	// Create .idea directory for PHPStorm
	ideaDir := filepath.Join(installPath, PHPStormDir)
	if err := os.MkdirAll(ideaDir, 0755); err != nil {
		return fmt.Errorf("failed to create .idea directory: %w", err)
	}

	// Create .idea/runConfigurations directory
	runConfDir := filepath.Join(installPath, PHPStormRunConfDir)
	if err := os.MkdirAll(runConfDir, 0755); err != nil {
		return fmt.Errorf("failed to create .idea/runConfigurations directory: %w", err)
	}

	// Write PHPStorm server configuration (php.xml)
	phpXmlPath := filepath.Join(ideaDir, "php.xml")
	if err := os.WriteFile(phpXmlPath, []byte(phpstormServerConfig), 0644); err != nil {
		return fmt.Errorf("failed to create PHPStorm php.xml: %w", err)
	}

	// Write PHPStorm run configuration
	runConfigPath := filepath.Join(runConfDir, "Listen_for_Xdebug.xml")
	if err := os.WriteFile(runConfigPath, []byte(phpstormRunConfig), 0644); err != nil {
		return fmt.Errorf("failed to create PHPStorm run configuration: %w", err)
	}

	logging.Print("Development environment configured (VSCode and PHPStorm)\n")
	return nil
}

// BuildXdebugImage builds the xdebug-enabled image
func BuildXdebugImage(installPath, orchestrator string) error {
	logging.Print("Building xdebug-enabled image...\n")

	files := orchestrators.GetDevComposeFiles(installPath)
	if err := orchestrators.BuildWithFiles(installPath, orchestrator, files...); err != nil {
		return fmt.Errorf("failed to build xdebug image: %w", err)
	}

	logging.Print("Xdebug image built successfully\n")
	return nil
}

// SetupFullDevMode performs the complete dev mode setup:
// 1. Extract code from container (must happen before creating docker-compose.dev.yml)
// 2. Create dev mode files (Dockerfile.xdebug, docker-compose.dev.yml, xdebug.ini)
// 3. Setup environment (.env, VSCode config)
// 4. Build xdebug image
// imageTag specifies the Canasta image tag to use (e.g., "latest", "dev", "1.39")
func SetupFullDevMode(installPath, orchestrator, imageTag string) error {
	// Extract MediaWiki code FIRST, before creating docker-compose.dev.yml
	// (docker-compose.dev.yml mounts ./mediawiki-code which must exist)
	// Uses raw docker commands to avoid docker-compose bind mount validation
	if err := ExtractMediaWikiCode(installPath, orchestrator, imageTag); err != nil {
		return err
	}

	// Create dev mode files (now that mediawiki-code directory exists)
	if err := CreateDevModeFiles(installPath); err != nil {
		return err
	}

	// Setup dev environment (.env, VSCode config)
	if err := SetupDevEnvironment(installPath, imageTag); err != nil {
		return err
	}

	// Build xdebug image
	if err := BuildXdebugImage(installPath, orchestrator); err != nil {
		return err
	}

	logging.Print(fmt.Sprintf("Using Canasta image tag: %s\n", imageTag))
	return nil
}

// IsDevModeSetup checks if dev mode files exist in the installation
func IsDevModeSetup(installPath string) bool {
	devComposePath := filepath.Join(installPath, DevComposeFile)
	dockerfilePath := filepath.Join(installPath, "Dockerfile.xdebug")

	_, err1 := os.Stat(devComposePath)
	_, err2 := os.Stat(dockerfilePath)

	return err1 == nil && err2 == nil
}

// EnableDevMode enables dev mode on an existing installation
// If dev mode is already set up, it returns immediately
// imageTag specifies the Canasta image tag to use (e.g., "latest", "dev", "1.39")
func EnableDevMode(installPath, orchestrator, imageTag string) error {
	if IsDevModeSetup(installPath) {
		logging.Print("Dev mode files already exist.\n")
		return nil
	}

	// Full dev mode setup needed
	logging.Print("Setting up dev mode for existing installation...\n")
	return SetupFullDevMode(installPath, orchestrator, imageTag)
}

