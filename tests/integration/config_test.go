//go:build integration

package integration

import (
	"strings"
	"testing"
	"time"
)

// TestConfig_GetSetDelete exercises config set, get (single key and all keys),
// and unset, verifying that values are correctly stored and removed.
func TestConfig_GetSetDelete(t *testing.T) {
	inst := createTestInstance(t, "inttest-config")

	// Create the instance
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

	// Wait for the wiki to be ready
	waitForWiki(t, inst.HTTPPort, 5*time.Minute)

	// Set a known configuration key
	out, err = inst.run(t, "config", "set", "-i", inst.ID,
		"MW_SITE_SERVER=https://test.example.com", "--no-restart")
	if err != nil {
		t.Fatalf("config set MW_SITE_SERVER failed: %v\n%s", err, out)
	}

	// Get the specific key and verify the value
	out, err = inst.run(t, "config", "get", "-i", inst.ID, "MW_SITE_SERVER")
	if err != nil {
		t.Fatalf("config get MW_SITE_SERVER failed: %v\n%s", err, out)
	}
	if got := strings.TrimSpace(out); got != "https://test.example.com" {
		t.Errorf("config get MW_SITE_SERVER = %q, want %q", got, "https://test.example.com")
	}

	// Get all settings and verify our key is present
	out, err = inst.run(t, "config", "get", "-i", inst.ID)
	if err != nil {
		t.Fatalf("config get (all) failed: %v\n%s", err, out)
	}
	if !strings.Contains(out, "MW_SITE_SERVER=https://test.example.com") {
		t.Errorf("config get (all) should contain MW_SITE_SERVER=https://test.example.com, got:\n%s", out)
	}

	// Unset the key
	out, err = inst.run(t, "config", "unset", "-i", inst.ID,
		"MW_SITE_SERVER", "--no-restart")
	if err != nil {
		t.Fatalf("config unset MW_SITE_SERVER failed: %v\n%s", err, out)
	}

	// Verify the key is gone — config get should return an error
	out, err = inst.run(t, "config", "get", "-i", inst.ID, "MW_SITE_SERVER")
	if err == nil {
		t.Errorf("config get MW_SITE_SERVER should fail after unset, but got: %s", out)
	}
	if !strings.Contains(out, "not set") {
		t.Errorf("expected 'not set' in error output, got: %s", out)
	}
}
