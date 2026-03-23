package upgrade

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// composeVolumeName returns the Docker Compose volume name for a given
// instance path and compose volume name. Docker Compose v2 derives the
// project name by lowercasing the directory name and stripping characters
// that don't match [a-z0-9_-].
func composeVolumeName(installPath, volume string) string {
	project := strings.ToLower(filepath.Base(installPath))
	project = regexp.MustCompile(`[^a-z0-9_-]`).ReplaceAllString(project, "")
	return project + "_" + volume
}

// detectMySQL8Data checks whether the MySQL data volume contains MySQL 8.0
// binary data (mysql.ibd). This is a read-only check that can run while the
// database container is still running.
// Error return kept for future-proofing.
//
//nolint:unparam
func detectMySQL8Data(installPath string) (bool, error) {
	volName := composeVolumeName(installPath, "mysql-data-volume")
	_, err := execute.Run("", "docker", "run", "--rm",
		"-v", volName+":/data",
		"alpine", "test", "-f", "/data/mysql.ibd")
	return err == nil, nil
}

// dumpMySQL8Data starts a temporary mysql:8.0 container against the data
// volume and dumps all user databases. The containers must be stopped before
// calling this so the data volume is not locked. Returns the path to the
// host-side dump file.
func dumpMySQL8Data(installPath string) (string, error) {
	volName := composeVolumeName(installPath, "mysql-data-volume")

	// Read the database password from .env
	envPath := filepath.Join(installPath, ".env")
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return "", fmt.Errorf("failed to read .env: %w", err)
	}
	password := envVars["MYSQL_PASSWORD"]
	if password == "" {
		password = "mediawiki"
	}

	containerName := "canasta-mysql-migrate"

	// Start a temporary MySQL 8.0 container with the existing data volume
	fmt.Println("  Starting temporary MySQL 8.0 container for dump...")
	output, err := execute.Run("", "docker", "run", "-d",
		"--name", containerName,
		"-v", volName+":/var/lib/mysql",
		"-e", "MYSQL_ROOT_PASSWORD="+password,
		"mysql:8.0")
	if err != nil {
		return "", fmt.Errorf("failed to start temporary MySQL container: %s", output)
	}

	// Always clean up the temporary container
	defer func() {
		_, _ = execute.Run("", "docker", "rm", "-f", containerName)
	}()

	// Wait for MySQL to be ready
	fmt.Println("  Waiting for MySQL 8.0 to be ready...")
	output, err = execute.Run("", "docker", "exec", containerName,
		"mysqladmin", "ping", "-h", "localhost",
		"--user=root", "--password="+password,
		"--wait=60")
	if err != nil {
		return "", fmt.Errorf("MySQL 8.0 container failed to become ready: %s", output)
	}

	// List user databases (exclude system databases)
	fmt.Println("  Listing user databases...")
	output, err = execute.Run("", "docker", "exec", containerName,
		"mysql", "--user=root", "--password="+password,
		"--skip-column-names", "--batch",
		"-e", "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('mysql','information_schema','performance_schema','sys')")
	if err != nil {
		return "", fmt.Errorf("failed to list databases: %s", output)
	}

	var databases []string
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		db := strings.TrimSpace(line)
		// Skip empty lines and MySQL warning/error messages that end up in
		// the combined stdout+stderr output from execute.Run.
		if db == "" || strings.Contains(db, "[Warning]") || strings.Contains(db, "[Error]") || strings.Contains(db, " ") {
			continue
		}
		databases = append(databases, db)
	}

	if len(databases) == 0 {
		fmt.Println("  No user databases found — skipping dump")
		return "", nil
	}

	fmt.Printf("  Dumping databases: %s\n", strings.Join(databases, ", "))

	// Dump all user databases into a single file inside the container
	dumpCmd := fmt.Sprintf("mysqldump --user=root --password=%s --databases %s --single-transaction --routines --triggers --events > /tmp/dump.sql",
		orchestrators.ShellQuote(password), strings.Join(databases, " "))
	output, err = execute.Run("", "docker", "exec", containerName,
		"bash", "-c", dumpCmd)
	if err != nil {
		return "", fmt.Errorf("mysqldump failed: %s", output)
	}

	// Copy dump to a temporary host file
	tmpFile, err := os.CreateTemp("", "canasta-mysql-dump-*.sql")
	if err != nil {
		return "", fmt.Errorf("failed to create temp file: %w", err)
	}
	tmpFile.Close()

	output, err = execute.Run("", "docker", "cp",
		containerName+":/tmp/dump.sql", tmpFile.Name())
	if err != nil {
		os.Remove(tmpFile.Name())
		return "", fmt.Errorf("failed to copy dump from container: %s", output)
	}

	fmt.Printf("  Dump saved to %s\n", tmpFile.Name())
	return tmpFile.Name(), nil
}

// clearMySQLDataVolume removes all files from the MySQL data volume so that
// MariaDB can initialize a fresh data directory on startup.
func clearMySQLDataVolume(installPath string) error {
	volName := composeVolumeName(installPath, "mysql-data-volume")
	fmt.Println("  Clearing MySQL data volume for fresh MariaDB initialization...")
	output, err := execute.Run("", "docker", "run", "--rm",
		"-v", volName+":/data",
		"alpine", "sh", "-c", "rm -rf /data/*")
	if err != nil {
		return fmt.Errorf("failed to clear MySQL data volume: %s", output)
	}
	return nil
}

// importMariaDBDump copies a SQL dump file into the running MariaDB container
// and imports it.
func importMariaDBDump(installPath string, orch orchestrators.Orchestrator, dumpPath string) error {
	fmt.Println("  Importing database dump into MariaDB...")

	// Copy dump into the db container
	if err := orch.CopyTo(installPath, orchestrators.ServiceDB, dumpPath, "/tmp/dump.sql"); err != nil {
		return fmt.Errorf("failed to copy dump to MariaDB container: %w", err)
	}

	// Read password for import
	envPath := filepath.Join(installPath, ".env")
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return fmt.Errorf("failed to read .env: %w", err)
	}
	password := envVars["MYSQL_PASSWORD"]
	if password == "" {
		password = "mediawiki"
	}

	// Import the dump
	importCmd := fmt.Sprintf("mariadb --no-defaults -uroot -p%s < /tmp/dump.sql",
		orchestrators.ShellQuote(password))
	_, err = orch.ExecWithError(installPath, orchestrators.ServiceDB, importCmd)
	if err != nil {
		return fmt.Errorf("failed to import dump into MariaDB: %w", err)
	}

	// Clean up the dump file inside the container
	_, _ = orch.ExecWithError(installPath, orchestrators.ServiceDB, "rm -f /tmp/dump.sql")

	fmt.Println("  Database import complete")
	return nil
}
