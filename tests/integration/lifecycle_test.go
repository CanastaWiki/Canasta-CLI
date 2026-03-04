//go:build integration

package integration

import (
	"strings"
	"testing"
	"time"
)

// TestLifecycle_CreateStartStopDelete exercises the full lifecycle:
// create → verify wiki is up → stop → start → verify wiki is back → delete → verify removed.
func TestLifecycle_CreateStartStopDelete(t *testing.T) {
	inst := createTestInstance(t, "inttest-lifecycle")

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

	// Stop the instance
	out, err = inst.run(t, "stop", "-i", inst.ID)
	if err != nil {
		t.Fatalf("canasta stop failed: %v\n%s", err, out)
	}

	// Start the instance again
	out, err = inst.run(t, "start", "-i", inst.ID)
	if err != nil {
		t.Fatalf("canasta start failed: %v\n%s", err, out)
	}

	// Verify the wiki comes back
	waitForWiki(t, inst.HTTPPort, 5*time.Minute)

	// Delete the instance
	out, err = inst.run(t, "delete", "-i", inst.ID, "-y")
	if err != nil {
		t.Fatalf("canasta delete failed: %v\n%s", err, out)
	}

	// Verify the instance is gone from the list
	out, err = runCanasta(t, inst.ConfigDir, "list")
	if err != nil {
		t.Fatalf("canasta list failed: %v\n%s", err, out)
	}
	if strings.Contains(out, inst.ID) {
		t.Errorf("instance %s still appears in list after delete:\n%s", inst.ID, out)
	}
}
