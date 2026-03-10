//go:build integration

package integration

import (
	"strings"
	"testing"
	"time"
)

// TestBackup_CreateAndRestore creates an instance, takes a backup, destroys
// the database, restores from backup, and verifies the wiki is accessible.
func TestBackup_CreateAndRestore(t *testing.T) {
	inst := createTestInstance(t, "inttest-backup")

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

	// Configure backup with a local filesystem repository
	backupDir := t.TempDir()
	out, err = inst.run(t, "config", "set", "-i", inst.ID, "RESTIC_REPOSITORY="+backupDir, "--no-restart")
	if err != nil {
		t.Fatalf("config set RESTIC_REPOSITORY failed: %v\n%s", err, out)
	}
	out, err = inst.run(t, "config", "set", "-i", inst.ID, "RESTIC_PASSWORD=testpass", "--no-restart")
	if err != nil {
		t.Fatalf("config set RESTIC_PASSWORD failed: %v\n%s", err, out)
	}

	// Initialize the backup repository
	out, err = inst.run(t, "backup", "init", "-i", inst.ID)
	if err != nil {
		t.Fatalf("backup init failed: %v\n%s", err, out)
	}

	// Create a backup
	out, err = inst.run(t, "backup", "create", "-i", inst.ID, "-t", "test-snapshot")
	if err != nil {
		t.Fatalf("backup create failed: %v\n%s", err, out)
	}

	// List backups and extract the snapshot ID
	out, err = inst.run(t, "backup", "list", "-i", inst.ID)
	if err != nil {
		t.Fatalf("backup list failed: %v\n%s", err, out)
	}
	snapshotID := extractSnapshotID(t, out, "test-snapshot")

	// Destroy the database to simulate data loss
	out, err = inst.run(t, "maintenance", "exec", "-i", inst.ID, "--",
		"bash", "-c", "mariadb -h db -u root -p$MYSQL_PASSWORD -e 'DROP DATABASE main; CREATE DATABASE main;'")
	if err != nil {
		t.Fatalf("failed to drop database: %v\n%s", err, out)
	}

	// Restore from backup
	out, err = inst.run(t, "backup", "restore", "-i", inst.ID, "-s", snapshotID, "--skip-safety-backup")
	if err != nil {
		t.Fatalf("backup restore failed: %v\n%s", err, out)
	}

	// Verify the wiki is accessible after restore
	waitForWiki(t, inst.HTTPPort, 3*time.Minute)
}

// extractSnapshotID parses restic list output to find a snapshot ID matching
// the given tag substring. Returns the first field of the matching line.
func extractSnapshotID(t *testing.T, output, tag string) string {
	t.Helper()
	for _, line := range strings.Split(output, "\n") {
		if strings.Contains(line, tag) {
			fields := strings.Fields(line)
			if len(fields) > 0 {
				return fields[0]
			}
		}
	}
	t.Fatalf("no snapshot found with tag %q in output:\n%s", tag, output)
	return ""
}
