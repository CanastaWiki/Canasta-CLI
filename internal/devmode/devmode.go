package devmode

import (
	_ "embed"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

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
// baseImage is the full image name (e.g., "ghcr.io/canastawiki/canasta:latest" or "canasta:local")
func ExtractMediaWikiCode(installPath, baseImage string) error {
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

	// Pull the image if it's from a registry (contains a slash)
	// Local images (e.g., "canasta:local") don't need pulling
	if strings.Contains(baseImage, "/") {
		logging.Print(fmt.Sprintf("Pulling image %s...\n", baseImage))
		if err, output := execute.Run("", "docker", "pull", baseImage); err != nil {
			return fmt.Errorf("failed to pull image: %s", output)
		}
	} else {
		logging.Print(fmt.Sprintf("Using local image %s...\n", baseImage))
	}

	// Create the destination directory
	if err := os.MkdirAll(codeDir, 0755); err != nil {
		return fmt.Errorf("failed to create code directory: %w", err)
	}

	// Start a container and run the symlinks script to create extension/skin symlinks
	// (normally /create-symlinks.sh runs as part of the entrypoint, but we bypass it with sleep)
	containerName := "canasta-code-extract-temp"
	logging.Print("Starting temporary container for code extraction...\n")
	if err, output := execute.Run("", "docker", "run", "-d", "--name", containerName, baseImage, "sleep", "infinity"); err != nil {
		return fmt.Errorf("failed to start temporary container: %s", output)
	}

	// Run the symlinks script to create extensions/ and skins/ symlinks
	logging.Print("Creating extension and skin symlinks...\n")
	if err, output := execute.Run("", "docker", "exec", containerName, "/create-symlinks.sh"); err != nil {
		_, _ = execute.Run("", "docker", "rm", "-f", containerName)
		return fmt.Errorf("failed to create symlinks: %s", output)
	}

	// Copy code from container to host, preserving symlinks
	// CanastaBase uses relative symlinks (../canasta-extensions/X, ../user-extensions/X)
	// Note: Can't use execute.Run here because it wraps commands in bash -c which breaks pipes
	logging.Print(fmt.Sprintf("Copying MediaWiki code to %s...\n", codeDir))
	tarCmd := fmt.Sprintf("docker exec %s tar -cf - -C /var/www/mediawiki/w . | tar -xf - -C %s", containerName, codeDir)
	cmd := exec.Command("bash", "-c", tarCmd)
	if output, err := cmd.CombinedOutput(); err != nil {
		// Clean up container on failure
		_, _ = execute.Run("", "docker", "rm", "-f", containerName)
		return fmt.Errorf("failed to copy code from container: %s (output: %s)", err, string(output))
	}

	// Remove the temporary container
	logging.Print("Removing temporary container...\n")
	if err, output := execute.Run("", "docker", "rm", "-f", containerName); err != nil {
		return fmt.Errorf("failed to remove temporary container: %s", output)
	}

	// Consolidate user extensions/skins into mediawiki-code
	// The extracted code has user-extensions/ and user-skins/ directories
	// We copy any existing user extensions from installPath/extensions/ into mediawiki-code/user-extensions/
	// Then replace installPath/extensions/ with a symlink to mediawiki-code/user-extensions/
	// This ensures:
	//   1. Symlinks in extensions/ (e.g., CommentStreams -> ../user-extensions/CommentStreams) resolve correctly
	//   2. Users can edit in either extensions/ or mediawiki-code/user-extensions/ (they're the same)
	//   3. IDE path mappings are simple: mediawiki-code/ <-> /var/www/mediawiki/w/
	logging.Print("Consolidating user extensions into mediawiki-code...\n")
	if err := consolidateUserDir(installPath, codeDir, "extensions", "user-extensions"); err != nil {
		return fmt.Errorf("failed to consolidate extensions: %w", err)
	}
	if err := consolidateUserDir(installPath, codeDir, "skins", "user-skins"); err != nil {
		return fmt.Errorf("failed to consolidate skins: %w", err)
	}

	logging.Print(fmt.Sprintf("MediaWiki code extracted to %s\n", codeDir))
	return nil
}

// consolidateUserDir copies user extensions/skins from installPath/dirName to codeDir/userDirName
// then replaces installPath/dirName with a symlink to codeDir/userDirName
// dirName is "extensions" or "skins", userDirName is "user-extensions" or "user-skins"
func consolidateUserDir(installPath, codeDir, dirName, userDirName string) error {
	sourceDir := filepath.Join(installPath, dirName)
	targetDir := filepath.Join(codeDir, userDirName)

	// Ensure target directory exists (should exist from extraction)
	if err := os.MkdirAll(targetDir, 0755); err != nil {
		return fmt.Errorf("failed to create %s directory: %w", userDirName, err)
	}

	// Check if source is already a symlink (dev mode already set up)
	if info, err := os.Lstat(sourceDir); err == nil && info.Mode()&os.ModeSymlink != 0 {
		logging.Print(fmt.Sprintf("  %s/ is already a symlink, skipping consolidation\n", dirName))
		return nil
	}

	// Copy contents from source to target (if source exists and has content)
	if info, err := os.Stat(sourceDir); err == nil && info.IsDir() {
		entries, err := os.ReadDir(sourceDir)
		if err != nil {
			return fmt.Errorf("failed to read %s directory: %w", dirName, err)
		}

		for _, entry := range entries {
			srcPath := filepath.Join(sourceDir, entry.Name())
			dstPath := filepath.Join(targetDir, entry.Name())

			// Remove existing destination if it exists (extensions/ takes precedence)
			if _, err := os.Stat(dstPath); err == nil {
				logging.Print(fmt.Sprintf("  Replacing %s in %s with version from %s/\n", entry.Name(), userDirName, dirName))
				if err := os.RemoveAll(dstPath); err != nil {
					return fmt.Errorf("failed to remove existing %s: %w", entry.Name(), err)
				}
			} else {
				logging.Print(fmt.Sprintf("  Copying %s to %s...\n", entry.Name(), userDirName))
			}

			// Copy directory recursively using cp -r
			if err, output := execute.Run("", "cp", "-r", srcPath, dstPath); err != nil {
				return fmt.Errorf("failed to copy %s: %s", entry.Name(), output)
			}
		}

		// Remove the original directory
		if err := os.RemoveAll(sourceDir); err != nil {
			return fmt.Errorf("failed to remove original %s directory: %w", dirName, err)
		}
	}

	// Create symlink: installPath/extensions -> mediawiki-code/user-extensions
	// Use relative path so it works regardless of absolute path
	relTarget := filepath.Join(CodeDir, userDirName)
	logging.Print(fmt.Sprintf("  Creating symlink %s -> %s\n", dirName, relTarget))
	if err := os.Symlink(relTarget, sourceDir); err != nil {
		return fmt.Errorf("failed to create %s symlink: %w", dirName, err)
	}

	return nil
}

// SetupDevEnvironment configures .env and creates IDE configs (VSCode and PHPStorm)
// baseImage is the full image name (e.g., "ghcr.io/canastawiki/canasta:latest" or "canasta:local")
func SetupDevEnvironment(installPath, baseImage string) error {
	logging.Print("Setting up development environment...\n")

	// Update .env file to set DEV_CODE_PATH and CANASTA_IMAGE
	envPath := filepath.Join(installPath, ".env")
	envContent, err := os.ReadFile(envPath)
	if err != nil {
		return fmt.Errorf("failed to read .env file: %w", err)
	}

	// Append dev settings to .env
	devSettings := fmt.Sprintf("\n# Development mode settings\nDEV_CODE_PATH=./mediawiki-code\nCANASTA_IMAGE=%s\n", baseImage)
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

// BuildXdebugImage builds the xdebug-enabled image.
// Dev mode requires Docker Compose for Build and GetDevFiles.
func BuildXdebugImage(installPath string, orch orchestrators.Orchestrator) error {
	compose, ok := orch.(*orchestrators.ComposeOrchestrator)
	if !ok {
		return fmt.Errorf("dev mode is only supported with Docker Compose")
	}

	logging.Print("Building xdebug-enabled image...\n")

	files := compose.GetDevFiles(installPath)
	if err := compose.Build(installPath, files...); err != nil {
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
// baseImage is the full Canasta image name (e.g., "ghcr.io/canastawiki/canasta:latest" or "canasta:local")
func SetupFullDevMode(installPath string, orch orchestrators.Orchestrator, baseImage string) error {
	// Extract MediaWiki code FIRST, before creating docker-compose.dev.yml
	// (docker-compose.dev.yml mounts ./mediawiki-code which must exist)
	// Uses raw docker commands to avoid docker-compose bind mount validation
	if err := ExtractMediaWikiCode(installPath, baseImage); err != nil {
		return err
	}

	// Create dev mode files (now that mediawiki-code directory exists)
	if err := CreateDevModeFiles(installPath); err != nil {
		return err
	}

	// Setup dev environment (.env, VSCode config)
	if err := SetupDevEnvironment(installPath, baseImage); err != nil {
		return err
	}

	// Build xdebug image
	if err := BuildXdebugImage(installPath, orch); err != nil {
		return err
	}

	logging.Print(fmt.Sprintf("Using Canasta base image: %s\n", baseImage))
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
// If dev mode files exist but symlinks need to be restored, it handles that
// baseImage is the full Canasta image name (e.g., "ghcr.io/canastawiki/canasta:latest" or "canasta:local")
func EnableDevMode(installPath string, orch orchestrators.Orchestrator, baseImage string) error {
	codeDir := filepath.Join(installPath, CodeDir)

	if IsDevModeSetup(installPath) {
		logging.Print("Dev mode files already exist.\n")

		// Even if dev mode files exist, we need to ensure symlinks are set up
		// (they may have been converted back to real directories by --no-dev)
		if err := ensureDevModeSymlinks(installPath, codeDir); err != nil {
			return err
		}
		return nil
	}

	// Full dev mode setup needed
	logging.Print("Setting up dev mode for existing installation...\n")
	return SetupFullDevMode(installPath, orch, baseImage)
}

// ensureDevModeSymlinks ensures extensions/ and skins/ are symlinks to mediawiki-code/
// This is needed when re-enabling dev mode after it was disabled with --no-dev
func ensureDevModeSymlinks(installPath, codeDir string) error {
	// Check if extensions/ is already a symlink
	extPath := filepath.Join(installPath, "extensions")
	if info, err := os.Lstat(extPath); err == nil && info.Mode()&os.ModeSymlink == 0 {
		// It's a real directory, need to consolidate
		logging.Print("Restoring dev mode symlinks...\n")
		if err := consolidateUserDir(installPath, codeDir, "extensions", "user-extensions"); err != nil {
			return fmt.Errorf("failed to consolidate extensions: %w", err)
		}
		if err := consolidateUserDir(installPath, codeDir, "skins", "user-skins"); err != nil {
			return fmt.Errorf("failed to consolidate skins: %w", err)
		}
	}
	return nil
}

// DisableDevMode disables dev mode on an installation
// This reverses the symlinks created by EnableDevMode, restoring extensions/ and skins/
// as real directories with their contents copied from mediawiki-code/
func DisableDevMode(installPath string) error {
	logging.Print("Disabling dev mode...\n")

	// Restore extensions/ and skins/ from symlinks to real directories
	if err := restoreUserDir(installPath, "extensions", "user-extensions"); err != nil {
		return fmt.Errorf("failed to restore extensions: %w", err)
	}
	if err := restoreUserDir(installPath, "skins", "user-skins"); err != nil {
		return fmt.Errorf("failed to restore skins: %w", err)
	}

	logging.Print("Dev mode disabled. User extensions/skins restored to their original directories.\n")
	logging.Print("Note: mediawiki-code/ directory is preserved. Delete it manually if no longer needed.\n")
	return nil
}

// restoreUserDir restores installPath/dirName from a symlink back to a real directory
// by copying contents from codeDir/userDirName
func restoreUserDir(installPath, dirName, userDirName string) error {
	symlinkPath := filepath.Join(installPath, dirName)
	sourceDir := filepath.Join(installPath, CodeDir, userDirName)

	// Check if it's actually a symlink
	info, err := os.Lstat(symlinkPath)
	if err != nil {
		// Directory doesn't exist, create empty one
		logging.Print(fmt.Sprintf("  %s/ doesn't exist, creating empty directory\n", dirName))
		return os.MkdirAll(symlinkPath, 0755)
	}

	if info.Mode()&os.ModeSymlink == 0 {
		// Not a symlink, nothing to do
		logging.Print(fmt.Sprintf("  %s/ is already a real directory\n", dirName))
		return nil
	}

	// Remove the symlink
	logging.Print(fmt.Sprintf("  Removing %s symlink...\n", dirName))
	if err := os.Remove(symlinkPath); err != nil {
		return fmt.Errorf("failed to remove symlink: %w", err)
	}

	// Create new directory
	if err := os.MkdirAll(symlinkPath, 0755); err != nil {
		return fmt.Errorf("failed to create directory: %w", err)
	}

	// Copy contents from mediawiki-code/user-extensions (if it exists)
	if _, err := os.Stat(sourceDir); err == nil {
		entries, err := os.ReadDir(sourceDir)
		if err != nil {
			return fmt.Errorf("failed to read source directory: %w", err)
		}

		for _, entry := range entries {
			srcPath := filepath.Join(sourceDir, entry.Name())
			dstPath := filepath.Join(symlinkPath, entry.Name())

			logging.Print(fmt.Sprintf("  Copying %s to %s/...\n", entry.Name(), dirName))
			if err, output := execute.Run("", "cp", "-r", srcPath, dstPath); err != nil {
				return fmt.Errorf("failed to copy %s: %s", entry.Name(), output)
			}
		}
	}

	return nil
}
