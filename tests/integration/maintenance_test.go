//go:build integration

package integration

import (
	"strings"
	"testing"
	"time"
)

// TestMaintenance_ExtensionScripts exercises the maintenance extension, script,
// and script execution subcommands against a running Canasta instance.
func TestMaintenance_ExtensionScripts(t *testing.T) {
	inst := createTestInstance(t, "inttest-maint")

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

	// List extensions with maintenance scripts — should include well-known extensions
	out, err = inst.run(t, "maintenance", "extension", "-i", inst.ID)
	if err != nil {
		t.Fatalf("maintenance extension (list) failed: %v\n%s", err, out)
	}
	// Canasta ships with many extensions; check for a few that are known to have
	// maintenance directories.
	for _, ext := range []string{"CirrusSearch", "SemanticMediaWiki"} {
		if strings.Contains(out, ext) {
			t.Logf("Found expected extension with maintenance scripts: %s", ext)
		}
		// Not a hard failure — the exact set depends on the image version.
	}
	// At minimum, the command should have listed something.
	if !strings.Contains(out, "Extensions with maintenance scripts") &&
		!strings.Contains(out, "No loaded extensions") {
		t.Errorf("unexpected output from maintenance extension list:\n%s", out)
	}

	// List available core maintenance scripts
	out, err = inst.run(t, "maintenance", "script", "-i", inst.ID)
	if err != nil {
		t.Fatalf("maintenance script (list) failed: %v\n%s", err, out)
	}
	// Verify well-known scripts are present
	for _, script := range []string{"update.php", "runJobs.php"} {
		if !strings.Contains(out, script) {
			t.Errorf("maintenance script list should contain %s, got:\n%s", script, out)
		}
	}

	// Run a maintenance script — showSiteStats.php produces output about site statistics
	out, err = inst.run(t, "maintenance", "script", "-i", inst.ID, "showSiteStats.php")
	if err != nil {
		t.Fatalf("maintenance script showSiteStats.php failed: %v\n%s", err, out)
	}
	// The script should produce some output (stats or at least a header)
	if strings.TrimSpace(out) == "" {
		t.Error("maintenance script showSiteStats.php produced no output")
	}
	t.Logf("showSiteStats.php output:\n%s", out)
}
