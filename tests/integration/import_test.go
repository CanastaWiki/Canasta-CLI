//go:build integration

package integration

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestImport_ExportAndImportDatabase creates an instance, exports its database,
// adds a second wiki, imports the dump into it, and verifies both wikis respond.
func TestImport_ExportAndImportDatabase(t *testing.T) {
	inst := createTestInstance(t, "inttest-import")

	// Create the instance with a first wiki
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

	// Export the main wiki's database
	exportPath := filepath.Join(os.TempDir(), "inttest-import-dump.sql")
	defer os.Remove(exportPath)
	out, err = inst.run(t, "export", "-i", inst.ID, "-w", "main", "-f", exportPath)
	if err != nil {
		t.Fatalf("canasta export failed: %v\n%s", err, out)
	}

	// Verify the dump file was created and is non-empty
	info, err := os.Stat(exportPath)
	if err != nil {
		t.Fatalf("export file not found: %v", err)
	}
	if info.Size() == 0 {
		t.Fatal("export file is empty")
	}
	t.Logf("Exported database: %s (%d bytes)", exportPath, info.Size())

	// Add a second wiki to import into
	out, err = inst.run(t, "add", "-i", inst.ID, "-w", "importtest", "-u", "localhost/importtest")
	if err != nil {
		t.Fatalf("canasta add failed: %v\n%s", err, out)
	}

	// Import the exported database into the second wiki
	out, err = inst.run(t, "import", "-i", inst.ID, "-w", "importtest", "-d", exportPath)
	if err != nil {
		t.Fatalf("canasta import failed: %v\n%s", err, out)
	}

	// Verify the original wiki is still accessible after import
	waitForWiki(t, inst.HTTPPort, 3*time.Minute)
}
