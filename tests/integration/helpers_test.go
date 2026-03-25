//go:build integration

package integration

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// canastaBin is the path to the built CLI binary, set once in TestMain.
var canastaBin string

// coverDir is the directory where the coverage-instrumented binary writes
// its coverage data. Each invocation appends a unique file, so all test
// runs can share a single directory without conflicts.
var coverDir string

// portCounter is an atomic counter for assigning unique ports to each test instance.
var portCounter uint32 = 10080

func TestMain(m *testing.M) {
	// Build the CLI binary once for all integration tests.
	// We use a dev build (no Version ldflags) so that `canasta upgrade`
	// skips the CLI self-update. We do set DefaultImageTag so the correct
	// Canasta image is pulled.
	tmpDir, err := os.MkdirTemp("", "canasta-integration-*")
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to create temp dir: %v\n", err)
		os.Exit(1)
	}

	binPath := filepath.Join(tmpDir, "canasta")
	covDataDir := filepath.Join(tmpDir, "covdata")
	if err := os.MkdirAll(covDataDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "failed to create coverage dir: %v\n", err)
		os.Exit(1)
	}

	// Read the CANASTA_VERSION file to set DefaultImageTag
	versionBytes, err := os.ReadFile("../../CANASTA_VERSION")
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to read CANASTA_VERSION file: %v\n", err)
		os.Exit(1)
	}
	imageTag := strings.TrimSpace(string(versionBytes))

	// Build with -cover so the binary writes coverage data to GOCOVERDIR
	// on each invocation. The -coverpkg flag ensures all packages are
	// instrumented, not just the main package.
	//
	// IMPORTANT: use the full module path, not a file path like canasta.go.
	// Coverage instrumentation is only applied when building a package.
	ldflags := fmt.Sprintf("-X 'github.com/CanastaWiki/Canasta-CLI/internal/canasta.DefaultImageTag=%s'", imageTag)
	cmd := exec.Command("go", "build",
		"-cover", "-coverpkg=github.com/CanastaWiki/Canasta-CLI/...",
		"-ldflags", ldflags,
		"-o", binPath, "github.com/CanastaWiki/Canasta-CLI")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "failed to build canasta binary: %v\n", err)
		os.Exit(1)
	}

	exitCode := m.Run()

	// Export coverage data collected from all binary invocations.
	exportIntegrationCoverage(covDataDir)

	os.RemoveAll(tmpDir)
	os.Exit(exitCode)
}

// exportIntegrationCoverage converts the binary coverage data written by
// the instrumented CLI into a standard Go coverage profile. The output
// path defaults to "integration-coverage.out" but can be overridden with
// the INTEGRATION_COVER_PROFILE environment variable.
func exportIntegrationCoverage(covDataDir string) {
	outFile := os.Getenv("INTEGRATION_COVER_PROFILE")
	if outFile == "" {
		outFile = "integration-coverage.out"
	}

	entries, err := os.ReadDir(covDataDir)
	if err != nil || len(entries) == 0 {
		fmt.Fprintf(os.Stderr, "no integration coverage data collected\n")
		return
	}

	cmd := exec.Command("go", "tool", "covdata", "textfmt",
		"-i="+covDataDir, "-o="+outFile)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "failed to convert coverage data: %v\n", err)
		return
	}
	fmt.Fprintf(os.Stderr, "integration coverage written to %s\n", outFile)
}

// testInstance holds the state for an isolated integration test instance.
type testInstance struct {
	ID        string
	WorkDir   string // temp directory for the instance
	ConfigDir string // isolated config directory (CANASTA_CONFIG_DIR)
	HTTPPort  string
	HTTPSPort string
	EnvFile   string // path to the test .env file
}

// nextPort returns the next available port pair (HTTP, HTTPS) for test isolation.
func nextPort() (httpPort, httpsPort string) {
	base := atomic.AddUint32(&portCounter, 10)
	return fmt.Sprintf("%d", base), fmt.Sprintf("%d", base+1)
}

// createTestInstance sets up an isolated test instance with unique ports and config dir.
// It registers a cleanup function that runs `canasta delete -y` even on test failure.
//
// workDir is created manually (not via t.TempDir) because containers create files
// owned by www-data inside the instance directory. Go's TempDir cleanup uses
// os.RemoveAll which would fail on those files. Instead, cleanup uses sudo rm -rf
// as a fallback after canasta delete.
func createTestInstance(t *testing.T, id string) *testInstance {
	t.Helper()

	workDir, err := os.MkdirTemp("", "canasta-int-work-*")
	if err != nil {
		t.Fatalf("failed to create work dir: %v", err)
	}
	// configDir only contains conf.json written by the CLI (host-owned), so t.TempDir is fine
	configDir := t.TempDir()
	httpPort, httpsPort := nextPort()

	// Write a test .env file with isolated ports and HTTPS off for health checks
	envFile := filepath.Join(workDir, "test.env")
	envContent := fmt.Sprintf(
		"HTTP_PORT=%s\nHTTPS_PORT=%s\nCADDY_AUTO_HTTPS=off\n",
		httpPort, httpsPort,
	)
	if err := os.WriteFile(envFile, []byte(envContent), 0644); err != nil {
		t.Fatalf("failed to write test .env: %v", err)
	}

	inst := &testInstance{
		ID:        id,
		WorkDir:   workDir,
		ConfigDir: configDir,
		HTTPPort:  httpPort,
		HTTPSPort: httpsPort,
		EnvFile:   envFile,
	}

	// Register cleanup to delete the instance even if the test fails or panics.
	// canasta delete handles most www-data owned files by cleaning from inside the
	// container, but sudo rm -rf is used as a fallback for any leftovers.
	t.Cleanup(func() {
		t.Logf("Cleanup: deleting instance %s", id)
		out, err := inst.run(t, "delete", "-i", id, "-y")
		if err != nil {
			t.Logf("Cleanup delete failed (may already be deleted): %s\n%s", err, out)
		}
		// Force-remove the work dir: containers create files owned by www-data
		// that os.RemoveAll cannot delete without group membership.
		rmCmd := exec.Command("sudo", "rm", "-rf", workDir)
		if out, err := rmCmd.CombinedOutput(); err != nil {
			t.Logf("sudo rm -rf %s failed: %v\n%s", workDir, err, out)
		}
	})

	return inst
}

// run executes the canasta binary with the given arguments, using the instance's
// isolated config directory. Returns combined output and any error.
func (inst *testInstance) run(t *testing.T, args ...string) (string, error) {
	t.Helper()
	cmd := exec.Command(canastaBin, args...)
	cmd.Env = append(os.Environ(),
		"CANASTA_CONFIG_DIR="+inst.ConfigDir,
		"GOCOVERDIR="+coverDir,
	)
	cmd.Dir = inst.WorkDir
	out, err := cmd.CombinedOutput()
	t.Logf("canasta %v:\n%s", args, string(out))
	return string(out), err
}

// runCanasta executes the canasta binary with the given arguments (without a testInstance).
// Useful for commands that don't need instance isolation.
func runCanasta(t *testing.T, configDir string, args ...string) (string, error) {
	t.Helper()
	cmd := exec.Command(canastaBin, args...)
	cmd.Env = append(os.Environ(),
		"CANASTA_CONFIG_DIR="+configDir,
		"GOCOVERDIR="+coverDir,
	)
	out, err := cmd.CombinedOutput()
	t.Logf("canasta %v:\n%s", args, string(out))
	return string(out), err
}

// waitForWiki polls the MediaWiki API until it gets a valid siteinfo response
// or the timeout expires. Uses 127.0.0.1 to avoid IPv6 resolution issues, and
// sets Host: localhost so Caddy matches the request to the wiki site block.
func waitForWiki(t *testing.T, httpPort string, timeout time.Duration) {
	t.Helper()
	apiURL := fmt.Sprintf("http://127.0.0.1:%s/w/api.php?action=query&meta=siteinfo&format=json", httpPort)
	deadline := time.Now().Add(timeout)

	var lastErr string
	for time.Now().Before(deadline) {
		req, reqErr := http.NewRequest("GET", apiURL, nil)
		if reqErr != nil {
			t.Fatalf("failed to create request: %v", reqErr)
		}
		req.Host = "localhost"
		client := &http.Client{Timeout: 10 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			lastErr = fmt.Sprintf("connection error: %v", err)
			time.Sleep(5 * time.Second)
			continue
		}
		var result map[string]interface{}
		decodeErr := json.NewDecoder(resp.Body).Decode(&result)
		resp.Body.Close()
		if decodeErr != nil {
			lastErr = fmt.Sprintf("HTTP %d, decode error: %v", resp.StatusCode, decodeErr)
			time.Sleep(5 * time.Second)
			continue
		}
		if _, ok := result["query"]; ok {
			t.Logf("Wiki is up at port %s", httpPort)
			return
		}
		// Private wikis (DefaultSettings.php) return readapidenied for
		// anonymous requests — this still means MediaWiki is running.
		if errMap, ok := result["error"].(map[string]interface{}); ok {
			if errMap["code"] == "readapidenied" {
				t.Logf("Wiki is up at port %s (private wiki, read denied for anonymous)", httpPort)
				return
			}
		}
		lastErr = fmt.Sprintf("HTTP %d, no 'query' key in response", resp.StatusCode)
		time.Sleep(5 * time.Second)
	}

	// Dump diagnostics before failing
	t.Logf("waitForWiki timed out (last: %s). Dumping diagnostics:", lastErr)
	if out, err := exec.Command("docker", "ps", "-a").CombinedOutput(); err == nil {
		t.Logf("docker ps -a:\n%s", out)
	}
	if out, err := exec.Command("docker", "logs", "--tail=50", "caddy").CombinedOutput(); err == nil {
		t.Logf("caddy logs:\n%s", out)
	}
	t.Fatalf("wiki did not become ready at port %s within %v", httpPort, timeout)
}

// instanceEnvPath returns the path to the .env file inside the instance directory.
func (inst *testInstance) instanceEnvPath() string {
	return filepath.Join(inst.WorkDir, inst.ID, ".env")
}
