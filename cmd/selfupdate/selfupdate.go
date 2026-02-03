package selfupdate

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/cmd/version"
)

const (
	githubAPIURL = "https://api.github.com/repos/CanastaWiki/Canasta-CLI/releases/latest"
	installShURL = "https://raw.githubusercontent.com/CanastaWiki/Canasta-CLI/main/install.sh"
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
	latestVersion, err := getLatestVersion()
	if err != nil {
		return err
	}

	fmt.Printf("Current version: %s\n", displayVersion(currentVersion))
	fmt.Printf("Latest version:  %s\n", latestVersion)

	if currentVersion == "" {
		fmt.Println("Warning: running a dev build; proceeding with update to latest release.")
	} else if currentVersion == latestVersion {
		fmt.Printf("Already up to date (%s)\n", currentVersion)
		return nil
	}

	// Determine the path of the currently running binary
	execPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("failed to determine current binary path: %w", err)
	}
	execPath, err = filepath.EvalSymlinks(execPath)
	if err != nil {
		return fmt.Errorf("failed to resolve binary path: %w", err)
	}

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
	cmd := exec.Command("bash", tmpFile.Name(), "-v", ver, "-t", execPath, "--skip-checks")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("update failed: %w", err)
	}

	if currentVersion != "" {
		fmt.Printf("Updated Canasta CLI from %s to %s\n", currentVersion, latestVersion)
	} else {
		fmt.Printf("Updated Canasta CLI to %s\n", latestVersion)
	}

	return nil
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
