package imagebuild

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

const (
	// LocalBaseTag is the tag used for locally built CanastaBase images
	LocalBaseTag = "canasta-base:local"
	// LocalCanastaTag is the tag used for locally built Canasta images
	LocalCanastaTag = "canasta:local"
)

// BuildFromSource builds Canasta (and optionally CanastaBase) from local repositories.
// workspacePath should contain a "Canasta" directory (required) and optionally a "CanastaBase" directory.
// Returns the final Canasta image tag to use.
func BuildFromSource(workspacePath string) (string, error) {
	canastaBasePath := filepath.Join(workspacePath, "CanastaBase")
	canastaPath := filepath.Join(workspacePath, "Canasta")

	// Verify Canasta repo exists (required)
	if _, err := os.Stat(canastaPath); os.IsNotExist(err) {
		return "", fmt.Errorf("Canasta repo not found at %s", canastaPath)
	}

	// Check if Canasta has a Dockerfile
	canastaDockerfile := filepath.Join(canastaPath, "Dockerfile")
	if _, err := os.Stat(canastaDockerfile); os.IsNotExist(err) {
		return "", fmt.Errorf("Dockerfile not found in Canasta repo at %s", canastaDockerfile)
	}

	baseImage := "ghcr.io/canastawiki/canasta-base:latest"

	// Build CanastaBase if it exists
	if _, err := os.Stat(canastaBasePath); err == nil {
		canastaBaseDockerfile := filepath.Join(canastaBasePath, "Dockerfile")
		if _, err := os.Stat(canastaBaseDockerfile); err == nil {
			logging.Print("Building CanastaBase from source...\n")
			if err := buildImage(canastaBasePath, LocalBaseTag); err != nil {
				return "", fmt.Errorf("failed to build CanastaBase: %w", err)
			}
			baseImage = LocalBaseTag
			logging.Print(fmt.Sprintf("CanastaBase built successfully: %s\n", LocalBaseTag))
		} else {
			logging.Print("CanastaBase directory found but no Dockerfile, skipping...\n")
		}
	}

	// Build Canasta with the base image (local or remote)
	logging.Print("Building Canasta from source...\n")
	if err := buildCanastaImage(canastaPath, baseImage, LocalCanastaTag); err != nil {
		return "", fmt.Errorf("failed to build Canasta: %w", err)
	}
	logging.Print(fmt.Sprintf("Canasta built successfully: %s\n", LocalCanastaTag))

	return LocalCanastaTag, nil
}

// buildImage builds a Docker image from a Dockerfile in the given path
func buildImage(path, tag string) error {
	err, output := execute.Run(path, "docker", "build", "-t", tag, ".")
	if err != nil {
		return fmt.Errorf("%s", output)
	}
	return nil
}

// buildCanastaImage builds the Canasta image with a specified base image
// The Canasta Dockerfile must support a BASE_IMAGE build arg
func buildCanastaImage(path, baseImage, tag string) error {
	err, output := execute.Run(path, "docker", "build",
		"--build-arg", fmt.Sprintf("BASE_IMAGE=%s", baseImage),
		"-t", tag, ".")
	if err != nil {
		return fmt.Errorf("%s", output)
	}
	return nil
}
