package selfupdate

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/cmd/version"
)

type githubRelease struct {
	TagName string `json:"tag_name"`
}

func NewCmdCreate() *cobra.Command {
	var selfUpdateCmd = &cobra.Command{
		Use:   "self-update",
		Short: "Update the Canasta CLI to the latest version",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runSelfUpdate()
		},
	}
	return selfUpdateCmd
}

func runSelfUpdate() error {
	currentVersion := version.Version

	// Fetch latest release info from GitHub
	resp, err := http.Get("https://api.github.com/repos/CanastaWiki/Canasta-CLI/releases/latest")
	if err != nil {
		return fmt.Errorf("failed to check for updates: %w\nPlease check your network connectivity", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("failed to check for updates: GitHub API returned status %d", resp.StatusCode)
	}

	var release githubRelease
	if err := json.NewDecoder(resp.Body).Decode(&release); err != nil {
		return fmt.Errorf("failed to parse release info: %w", err)
	}

	latestVersion := release.TagName

	if currentVersion == "" {
		fmt.Println("Warning: running a dev build, cannot verify current version")
	} else if currentVersion == latestVersion {
		fmt.Printf("Already up to date (%s)\n", currentVersion)
		return nil
	}

	// Detect platform
	goos := runtime.GOOS
	goarch := runtime.GOARCH

	// Construct download URL
	downloadURL := fmt.Sprintf(
		"https://github.com/CanastaWiki/Canasta-CLI/releases/download/%s/canasta-%s-%s",
		latestVersion, goos, goarch,
	)

	fmt.Printf("Downloading %s for %s/%s...\n", latestVersion, goos, goarch)

	// Download to temp file
	dlResp, err := http.Get(downloadURL)
	if err != nil {
		return fmt.Errorf("failed to download update: %w\nPlease check your network connectivity", err)
	}
	defer dlResp.Body.Close()

	if dlResp.StatusCode != http.StatusOK {
		return fmt.Errorf("failed to download update: server returned status %d", dlResp.StatusCode)
	}

	tmpFile, err := os.CreateTemp("", "canasta-update-*")
	if err != nil {
		return fmt.Errorf("failed to create temp file: %w", err)
	}
	tmpPath := tmpFile.Name()
	defer os.Remove(tmpPath)

	if _, err := io.Copy(tmpFile, dlResp.Body); err != nil {
		tmpFile.Close()
		return fmt.Errorf("failed to write update: %w", err)
	}
	tmpFile.Close()

	if err := os.Chmod(tmpPath, 0755); err != nil {
		return fmt.Errorf("failed to set permissions: %w", err)
	}

	// Find current binary path (resolve symlinks)
	execPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("failed to determine current binary path: %w", err)
	}
	execPath, err = filepath.EvalSymlinks(execPath)
	if err != nil {
		return fmt.Errorf("failed to resolve binary path: %w", err)
	}

	// Replace current binary
	if err := os.Rename(tmpPath, execPath); err != nil {
		return fmt.Errorf("failed to replace binary: %w\nYou may need to run this command with sudo", err)
	}

	if currentVersion != "" {
		fmt.Printf("Updated Canasta CLI from %s to %s\n", currentVersion, latestVersion)
	} else {
		fmt.Printf("Updated Canasta CLI to %s\n", latestVersion)
	}

	return nil
}
