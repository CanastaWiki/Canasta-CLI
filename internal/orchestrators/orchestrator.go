package orchestrators

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

// Orchestrator defines the interface for container orchestration backends.
// Docker Compose is the primary implementation; Kubernetes support is planned.
// Compose-specific operations (Pull, Build, CopyOverrideFile, GetDevFiles)
// live on ComposeOrchestrator directly rather than on this interface.
type Orchestrator interface {
	CheckDependencies() error
	GetRepoLink() string
	Start(instance config.Installation) error
	Stop(instance config.Installation) error
	Update(installPath string) (*UpdateReport, error)
	Destroy(installPath string) (string, error)
	ExecWithError(installPath, service, command string) (string, error)
	ExecStreaming(installPath, service, command string) error
	CheckRunningStatus(instance config.Installation) error
	CopyFrom(installPath, service, containerPath, hostPath string) error
	CopyTo(installPath, service, hostPath, containerPath string) error
}

// ImageInfo holds information about a Docker image
type ImageInfo struct {
	Service string
	Image   string
	ID      string
}

// UpdateReport describes what changed during an Update operation
type UpdateReport struct {
	UpdatedImages   []ImageInfo // Images that were updated
	UnchangedImages []ImageInfo // Images that remained the same
}

// DeleteConfig removes the installation directory from disk.
// This is a pure filesystem operation, not orchestrator-specific.
func DeleteConfig(installPath string) (string, error) {
	err, output := execute.Run("", "rm", "-rf", installPath)
	return output, err
}

// Exec runs a command in a service container and fatals on error.
func Exec(orch Orchestrator, installPath, service, command string) string {
	output, err := orch.ExecWithError(installPath, service, command)
	if err != nil {
		logging.Fatal(fmt.Errorf("%s", output))
	}
	return output
}

// StopAndStart stops and then starts the containers for an installation.
func StopAndStart(orch Orchestrator, instance config.Installation) error {
	if err := orch.Stop(instance); err != nil {
		return err
	}
	return orch.Start(instance)
}

// EnsureRunning checks if containers are running and starts them if not.
func EnsureRunning(orch Orchestrator, instance config.Installation) error {
	if err := orch.CheckRunningStatus(instance); err != nil {
		logging.Print("Containers not running, starting them...\n")
		if err := orch.Start(instance); err != nil {
			return fmt.Errorf("failed to start containers: %w", err)
		}
	}
	return nil
}

// ImportDatabase copies a SQL dump into the db container and imports it.
func ImportDatabase(orch Orchestrator, databaseName, databasePath, dbPassword string, instance config.Installation) error {
	dbUser := "root"
	if dbPassword == "" {
		dbPassword = "mediawiki"
	}

	escapedPassword := strings.ReplaceAll(dbPassword, "'", "'\\''")

	isCompressed := strings.HasSuffix(databasePath, ".sql.gz")

	var containerFile string
	if isCompressed {
		containerFile = fmt.Sprintf("/tmp/%s.sql.gz", databaseName)
	} else {
		containerFile = fmt.Sprintf("/tmp/%s.sql", databaseName)
	}

	err := orch.CopyTo(instance.Path, "db", databasePath, containerFile)
	if err != nil {
		return fmt.Errorf("error copying database file to container: %w", err)
	}

	defer func() {
		rmCmdStr := fmt.Sprintf("rm -f /tmp/%s.sql /tmp/%s.sql.gz", databaseName, databaseName)
		_, _ = orch.ExecWithError(instance.Path, "db", rmCmdStr)
	}()

	if isCompressed {
		decompressCmd := fmt.Sprintf("gunzip -f %s", containerFile)
		_, err = orch.ExecWithError(instance.Path, "db", decompressCmd)
		if err != nil {
			return fmt.Errorf("error decompressing database file: %w", err)
		}
	}

	createCmdStr := fmt.Sprintf("mysql --no-defaults -u%s -p'%s' -e 'CREATE DATABASE IF NOT EXISTS %s'", dbUser, escapedPassword, databaseName)
	_, err = orch.ExecWithError(instance.Path, "db", createCmdStr)
	if err != nil {
		return fmt.Errorf("error creating database: %w", err)
	}

	importCmdStr := fmt.Sprintf("mysql --no-defaults -u%s -p'%s' %s < /tmp/%s.sql", dbUser, escapedPassword, databaseName, databaseName)
	_, err = orch.ExecWithError(instance.Path, "db", importCmdStr)
	if err != nil {
		return fmt.Errorf("error importing database: %w", err)
	}

	return nil
}
