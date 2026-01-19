package devmode

import (
	"embed"
	"fmt"
	"os"
	"path/filepath"

	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

const (
	DevComposeFile      = "docker-compose.dev.yml"
	MainComposeFile     = "docker-compose.yml"
	OverrideComposeFile = "docker-compose.override.yml"
	CodeDir             = "mediawiki-code"
	VSCodeDir           = ".vscode"
	PHPStormDir         = ".idea"
	PHPStormRunConfDir  = ".idea/runConfigurations"
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

// GetComposeFiles returns the list of compose files needed for dev mode
// It checks if docker-compose.override.yml exists and includes it
func GetComposeFiles(installPath string) []string {
	files := []string{MainComposeFile}

	// Include override file if it exists (contains port mappings)
	overridePath := filepath.Join(installPath, OverrideComposeFile)
	if _, err := os.Stat(overridePath); err == nil {
		files = append(files, OverrideComposeFile)
	}

	// Dev compose file goes last to override settings
	files = append(files, DevComposeFile)
	return files
}

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
func ExtractMediaWikiCode(installPath, orchestrator string) error {
	codeDir := filepath.Join(installPath, CodeDir)

	logging.Print("Extracting MediaWiki code for live editing...\n")

	// Create temporary container to extract code from
	if err := orchestrators.CreateContainer(installPath, orchestrator, "web"); err != nil {
		return fmt.Errorf("failed to create web container: %w", err)
	}

	// Create the destination directory
	if err := os.MkdirAll(codeDir, 0755); err != nil {
		return fmt.Errorf("failed to create code directory: %w", err)
	}

	// Copy code from container to host
	// Using /var/www/mediawiki/. to copy contents (not the directory itself)
	if err := orchestrators.CopyFromContainer(installPath, orchestrator, "web", "/var/www/mediawiki/.", codeDir); err != nil {
		// Clean up on failure
		orchestrators.RemoveContainer(installPath, orchestrator, "web")
		return fmt.Errorf("failed to copy code from container: %w", err)
	}

	// Remove the temporary container
	if err := orchestrators.RemoveContainer(installPath, orchestrator, "web"); err != nil {
		return fmt.Errorf("failed to remove temporary container: %w", err)
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

	files := GetComposeFiles(installPath)
	if err := orchestrators.BuildWithFiles(installPath, orchestrator, files...); err != nil {
		return fmt.Errorf("failed to build xdebug image: %w", err)
	}

	logging.Print("Xdebug image built successfully\n")
	return nil
}

// StartDev starts the installation in dev mode
func StartDev(installPath, orchestrator string) error {
	logging.Print("Starting in development mode...\n")
	files := GetComposeFiles(installPath)
	return orchestrators.StartWithFiles(installPath, orchestrator, files...)
}

// StopDev stops the installation in dev mode
func StopDev(installPath, orchestrator string) error {
	logging.Print("Stopping development mode...\n")
	files := GetComposeFiles(installPath)
	return orchestrators.StopWithFiles(installPath, orchestrator, files...)
}

// SetupFullDevMode performs the complete dev mode setup:
// 1. Create dev mode files (Dockerfile.xdebug, docker-compose.dev.yml, xdebug.ini)
// 2. Extract code from container
// 3. Setup environment (.env, VSCode config)
// 4. Build xdebug image
// imageTag specifies the Canasta image tag to use (e.g., "latest", "dev", "1.39")
func SetupFullDevMode(installPath, orchestrator, imageTag string) error {
	// Create all the dev mode files first
	if err := CreateDevModeFiles(installPath); err != nil {
		return err
	}

	// Extract MediaWiki code
	if err := ExtractMediaWikiCode(installPath, orchestrator); err != nil {
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
