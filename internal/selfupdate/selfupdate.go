package selfupdate

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"syscall"

	"github.com/CanastaWiki/Canasta-CLI/cmd/version"
)

const githubAPIURL = "https://api.github.com/repos/CanastaWiki/Canasta-CLI/releases/latest"

type githubRelease struct {
	TagName string `json:"tag_name"`
}

// CheckAndUpdate checks for a newer CLI version and updates if available.
// If an update is performed, it re-execs the current process with the new binary.
// Returns true if already up-to-date (caller should continue), or doesn't return
// if re-exec happens.
func CheckAndUpdate() (bool, error) {
	currentVersion := version.Version

	fmt.Println("Checking for CLI updates...")

	latestVersion, err := getLatestVersion()
	if err != nil {
		return false, err
	}

	if currentVersion == "" {
		fmt.Println("Skipping CLI update: running a dev build.")
		return true, nil
	} else if currentVersion == latestVersion {
		fmt.Printf("CLI is up to date (%s)\n", currentVersion)
		return true, nil
	}

	fmt.Printf("Updating CLI from %s to %s...\n", displayVersion(currentVersion), latestVersion)

	// Determine the path of the currently running binary
	execPath, err := os.Executable()
	if err != nil {
		return false, fmt.Errorf("failed to determine current binary path: %w", err)
	}
	execPath, err = filepath.EvalSymlinks(execPath)
	if err != nil {
		return false, fmt.Errorf("failed to resolve binary path: %w", err)
	}

	// Download and install the updated binary
	if err := downloadAndInstall(latestVersion, execPath); err != nil {
		return false, err
	}

	fmt.Printf("CLI updated to %s. Continuing with upgrade...\n\n", latestVersion)

	// Re-exec with the new binary, passing the same arguments
	return false, reexec(execPath)
}

func downloadAndInstall(latestVersion, execPath string) error {
	// Build download URL for the platform-specific binary
	url := fmt.Sprintf(
		"https://github.com/CanastaWiki/Canasta-CLI/releases/download/%s/canasta-%s-%s",
		latestVersion, runtime.GOOS, runtime.GOARCH,
	)

	tmpFile, err := os.CreateTemp("", ".canasta-update-*")
	if err != nil {
		return fmt.Errorf("failed to create temp file: %w", err)
	}
	tmpPath := tmpFile.Name()
	defer os.Remove(tmpPath)

	// URL is constructed from trusted constants and runtime.GOOS/GOARCH
	//nolint:gosec
	resp, err := http.Get(url)
	if err != nil {
		return fmt.Errorf("failed to download update: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("failed to download update: HTTP %d from %s", resp.StatusCode, url)
	}

	if _, err := io.Copy(tmpFile, resp.Body); err != nil {
		return fmt.Errorf("failed to write update: %w", err)
	}
	tmpFile.Close()

	// Preserve permissions of the existing binary
	info, err := os.Stat(execPath)
	if err != nil {
		return fmt.Errorf("failed to stat current binary: %w", err)
	}
	if err := os.Chmod(tmpPath, info.Mode()); err != nil {
		return fmt.Errorf("failed to set permissions: %w", err)
	}

	// Try direct rename first (works if same filesystem and user has write access)
	if err := os.Rename(tmpPath, execPath); err == nil {
		return nil
	}

	// Rename failed (permission denied or cross-filesystem) — use sudo mv
	fmt.Println("Elevated permissions required to update the binary.")
	cmd := exec.Command("sudo", "mv", tmpPath, execPath)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("failed to move updated binary to %s: %w", execPath, err)
	}

	return nil
}

func reexec(execPath string) error {
	// Re-exec with the same arguments
	args := os.Args

	// Use syscall.Exec to replace this process with the new binary
	return syscall.Exec(execPath, args, os.Environ())
}

func getLatestVersion() (string, error) {
	resp, err := http.Get(githubAPIURL)
	if err != nil {
		return "", fmt.Errorf("failed to check for updates: %w\nPlease check your network connectivity", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("failed to check for updates: GitHub API returned status %d", resp.StatusCode)
	}

	var release githubRelease
	if err := json.NewDecoder(resp.Body).Decode(&release); err != nil {
		return "", fmt.Errorf("failed to parse release info: %w", err)
	}

	if release.TagName == "" {
		return "", fmt.Errorf("no tag_name found in latest release")
	}

	return release.TagName, nil
}

func displayVersion(v string) string {
	if v == "" {
		return "dev"
	}
	return v
}
