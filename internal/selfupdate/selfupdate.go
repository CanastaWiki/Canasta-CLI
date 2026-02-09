package selfupdate

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/CanastaWiki/Canasta-CLI/cmd/version"
)

const (
	githubAPIURL = "https://api.github.com/repos/CanastaWiki/Canasta-CLI/releases/latest"
	installShURL = "https://raw.githubusercontent.com/CanastaWiki/Canasta-CLI/main/install.sh"
)

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
		fmt.Println("Warning: running a dev build; proceeding with update to latest release.")
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

	// Download and run install.sh to update the binary
	if err := downloadAndInstall(latestVersion, execPath); err != nil {
		return false, err
	}

	fmt.Printf("CLI updated to %s. Continuing with upgrade...\n\n", latestVersion)

	// Re-exec with the new binary, passing the same arguments
	return false, reexec(execPath)
}

func downloadAndInstall(latestVersion, execPath string) error {
	// Download install.sh to a temp file
	tmpFile, err := os.CreateTemp("", "canasta-install-*.sh")
	if err != nil {
		return fmt.Errorf("failed to create temp file: %w", err)
	}
	defer os.Remove(tmpFile.Name())

	resp, err := http.Get(installShURL)
	if err != nil {
		return fmt.Errorf("failed to download install script: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("failed to download install script: HTTP %d", resp.StatusCode)
	}

	if _, err := io.Copy(tmpFile, resp.Body); err != nil {
		return fmt.Errorf("failed to write install script: %w", err)
	}
	tmpFile.Close()

	// Strip "v" prefix for install.sh: "v1.58.0" -> "1.58.0"
	ver := strings.TrimPrefix(latestVersion, "v")

	// Run install.sh with flags to perform the update
	// We use a subprocess here rather than exec because we need to continue after
	bashPath, err := findBash()
	if err != nil {
		return err
	}

	// Use os/exec to run the install script
	cmd := &syscall.ProcAttr{
		Dir:   "",
		Env:   os.Environ(),
		Files: []uintptr{os.Stdin.Fd(), os.Stdout.Fd(), os.Stderr.Fd()},
	}

	pid, err := syscall.ForkExec(bashPath, []string{"bash", tmpFile.Name(), "-v", ver, "-t", execPath, "--skip-checks"}, cmd)
	if err != nil {
		return fmt.Errorf("failed to run install script: %w", err)
	}

	// Wait for the install script to complete
	var ws syscall.WaitStatus
	_, err = syscall.Wait4(pid, &ws, 0, nil)
	if err != nil {
		return fmt.Errorf("failed waiting for install script: %w", err)
	}

	if !ws.Exited() || ws.ExitStatus() != 0 {
		return fmt.Errorf("install script failed with status %d", ws.ExitStatus())
	}

	return nil
}

func reexec(execPath string) error {
	// Re-exec with the same arguments
	args := os.Args

	// Use syscall.Exec to replace this process with the new binary
	return syscall.Exec(execPath, args, os.Environ())
}

func findBash() (string, error) {
	paths := []string{"/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash"}
	for _, p := range paths {
		if _, err := os.Stat(p); err == nil {
			return p, nil
		}
	}
	return "", fmt.Errorf("bash not found")
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
