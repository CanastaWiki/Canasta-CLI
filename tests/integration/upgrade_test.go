//go:build integration

package integration

import (
	"os"
	"strings"
	"testing"
	"time"

	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
)

// TestUpgrade_MigrationsRun creates a fresh instance, simulates a pre-migration
// state by removing CANASTA_IMAGE from .env, then runs upgrade and verifies
// that migrations run and the wiki is still accessible afterward.
func TestUpgrade_MigrationsRun(t *testing.T) {
	inst := createTestInstance(t, "inttest-upgrade")

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

	// Verify the wiki is accessible
	waitForWiki(t, inst.HTTPPort, 5*time.Minute)

	// Simulate a pre-migration state: remove CANASTA_IMAGE from .env
	// so that the backfillCanastaImage migration will run during upgrade.
	envPath := inst.instanceEnvPath()
	envContent, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatalf("failed to read .env: %v", err)
	}
	var filteredLines []string
	for _, line := range strings.Split(string(envContent), "\n") {
		if !strings.HasPrefix(line, "CANASTA_IMAGE=") {
			filteredLines = append(filteredLines, line)
		}
	}
	//nolint:gosec
	if err := os.WriteFile(envPath, []byte(strings.Join(filteredLines, "\n")), permissions.FilePermission); err != nil {
		t.Fatalf("failed to write modified .env: %v", err)
	}

	// Run upgrade
	out, err = inst.run(t, "upgrade")
	if err != nil {
		t.Fatalf("canasta upgrade failed: %v\n%s", err, out)
	}

	// Verify migrations ran — output should mention CANASTA_IMAGE being set
	if !strings.Contains(out, "CANASTA_IMAGE") {
		t.Errorf("upgrade output does not mention CANASTA_IMAGE migration:\n%s", out)
	}

	// Verify CANASTA_IMAGE was backfilled in .env
	envContent, err = os.ReadFile(envPath)
	if err != nil {
		t.Fatalf("failed to read .env after upgrade: %v", err)
	}
	if !strings.Contains(string(envContent), "CANASTA_IMAGE=") {
		t.Errorf(".env does not contain CANASTA_IMAGE after upgrade:\n%s", string(envContent))
	}

	// Verify the wiki is still accessible after upgrade
	waitForWiki(t, inst.HTTPPort, 5*time.Minute)
}
