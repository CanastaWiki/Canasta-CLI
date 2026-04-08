//go:build integration

package integration

import (
	"fmt"
	"net/http"
	"testing"
	"time"
)

// waitForWikiAtPath polls the MediaWiki API at a specific path until it gets a
// valid response or the timeout expires. The path should include a leading slash
// (e.g., "/docs"). An empty path behaves like waitForWiki.
func waitForWikiAtPath(t *testing.T, httpPort, path string, timeout time.Duration) {
	t.Helper()
	apiURL := fmt.Sprintf("http://127.0.0.1:%s%s/w/api.php?action=query&meta=siteinfo&format=json", httpPort, path)
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
		resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			t.Logf("Wiki is up at port %s path %s", httpPort, path)
			return
		}
		lastErr = fmt.Sprintf("HTTP %d", resp.StatusCode)
		time.Sleep(5 * time.Second)
	}

	t.Fatalf("wiki at path %q did not become ready at port %s within %v (last: %s)", path, httpPort, timeout, lastErr)
}

// wikiNotAccessibleAtPath verifies that the wiki at the given path is NOT
// accessible (connection refused, timeout, or non-200 status).
func wikiNotAccessibleAtPath(t *testing.T, httpPort, path string) {
	t.Helper()
	apiURL := fmt.Sprintf("http://127.0.0.1:%s%s/w/api.php?action=query&meta=siteinfo&format=json", httpPort, path)
	req, err := http.NewRequest("GET", apiURL, nil)
	if err != nil {
		t.Fatalf("failed to create request: %v", err)
	}
	req.Host = "localhost"
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		// Connection refused or similar — wiki is down, which is expected.
		t.Logf("Wiki at path %q is not accessible (expected): %v", path, err)
		return
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Logf("Wiki at path %q returned HTTP %d (expected non-200)", path, resp.StatusCode)
		return
	}
	t.Errorf("wiki at path %q is still accessible (HTTP 200), expected it to be removed", path)
}

// TestFarm_AddAndRemoveWiki creates an instance with a main wiki, adds a second
// wiki at a subpath, verifies both are accessible, removes the second wiki, and
// confirms the main wiki still works while the second wiki is gone.
func TestFarm_AddAndRemoveWiki(t *testing.T) {
	inst := createTestInstance(t, "inttest-farm")

	// Create the instance with the main wiki
	out, err := inst.run(t, "create",
		"-i", inst.ID,
		"-w", "main",
		"-n", "localhost",
		"-p", inst.WorkDir,
		"-e", inst.EnvFile,
	)
	if err != nil {
		t.Fatalf("canasta create failed: %v\n%s", err, out)
	}

	// Wait for the main wiki to be accessible
	waitForWiki(t, inst.HTTPPort, 5*time.Minute)

	// Add a second wiki at /docs
	wikiURL := fmt.Sprintf("localhost:%s/docs", inst.HTTPPort)
	out, err = inst.run(t, "add",
		"-i", inst.ID,
		"-w", "docs",
		"-u", wikiURL,
	)
	if err != nil {
		t.Fatalf("canasta add failed: %v\n%s", err, out)
	}

	// Wait for the docs wiki to be accessible
	waitForWikiAtPath(t, inst.HTTPPort, "/docs", 5*time.Minute)

	// Verify main wiki still responds
	waitForWiki(t, inst.HTTPPort, 2*time.Minute)

	// Remove the docs wiki
	out, err = inst.run(t, "remove",
		"-i", inst.ID,
		"-w", "docs",
		"-y",
	)
	if err != nil {
		t.Fatalf("canasta remove failed: %v\n%s", err, out)
	}

	// Wait for restart to settle
	waitForWiki(t, inst.HTTPPort, 2*time.Minute)

	// Verify main wiki still responds after removing docs
	waitForWiki(t, inst.HTTPPort, 2*time.Minute)

	// Verify docs wiki no longer responds
	wikiNotAccessibleAtPath(t, inst.HTTPPort, "/docs")
}
