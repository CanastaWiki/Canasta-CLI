package orchestrators

import (
	"fmt"
	"os"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

// currentUser returns "UID:GID" for the current process, used with
// docker run --user so that files written to bind-mounted host paths
// are owned by the invoking user rather than root.
func currentUser() string {
	return fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid())
}

// Service name constants for container orchestration.
const (
	ServiceWeb = "web"
	ServiceDB  = "db"
)

// Orchestrator defines the interface for container orchestration backends.
// Docker Compose is the primary implementation; Kubernetes support is planned.
// Compose-specific operations (Pull, Build, CopyOverrideFile, GetDevFiles)
// live on ComposeOrchestrator directly rather than on this interface.
type Orchestrator interface {
	CheckDependencies() error
	WriteStackFiles(installPath string) error
	UpdateStackFiles(installPath string, dryRun bool) (bool, error)
	Start(instance config.Instance) error
	Stop(instance config.Instance) error
	Update(installPath string) (*UpdateReport, error)
	Destroy(installPath string) (string, error)
	ExecWithError(installPath, service, command string) (string, error)
	ExecStreaming(installPath, service, command string) error
	CheckRunningStatus(instance config.Instance) error
	CopyFrom(installPath, service, containerPath, hostPath string) error
	CopyTo(installPath, service, hostPath, containerPath string) error
	RunBackup(installPath, envPath string, volumes map[string]string, args ...string) (string, error)
	RestoreFromBackupVolume(installPath string, dirs map[string]string) error

	// InitConfig sets up orchestrator-specific configuration for a new instance.
	// Called once during "canasta create" after wikis.yaml and .env are in place.
	InitConfig(installPath string) error

	// UpdateConfig regenerates orchestrator-specific configuration after
	// changes to wikis.yaml (e.g., adding or removing a wiki).
	UpdateConfig(installPath string) error

	// MigrateConfig applies orchestrator-specific migration steps during
	// "canasta upgrade". Returns true if any changes were made.
	MigrateConfig(installPath string, dryRun bool) (bool, error)

	// ListServices returns the names of running services for the instance.
	ListServices(instance config.Instance) ([]string, error)

	// ExecInteractive runs an interactive command in a service container with
	// stdin/stdout/stderr attached. If command is nil, defaults to a shell.
	ExecInteractive(instance config.Instance, service string, command []string) error

	// Name returns a human-readable name for the orchestrator (e.g. "Docker Compose").
	Name() string

	// SupportsDevMode reports whether this orchestrator supports development mode (Xdebug).
	SupportsDevMode() bool

	// SupportsImagePull reports whether this orchestrator supports pulling container images.
	SupportsImagePull() bool
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

// DeleteConfig removes the instance directory from disk.
// This is a pure filesystem operation, not orchestrator-specific.
func DeleteConfig(installPath string) (string, error) {
	output, err := execute.Run("", "rm", "-rf", installPath)
	return output, err
}

// Exec runs a command in a service container and returns an error on failure.
func Exec(orch Orchestrator, installPath, service, command string) (string, error) {
	output, err := orch.ExecWithError(installPath, service, command)
	if err != nil {
		return output, fmt.Errorf("command failed in service %q: %s", service, output)
	}
	return output, nil
}

// StopAndStart stops and then starts the containers for an instance.
func StopAndStart(orch Orchestrator, instance config.Instance) error {
	if err := orch.Stop(instance); err != nil {
		return err
	}
	return orch.Start(instance)
}

// EnsureRunning checks if containers are running and starts them if not.
func EnsureRunning(orch Orchestrator, instance config.Instance) error {
	if err := orch.CheckRunningStatus(instance); err != nil {
		logging.Print("Containers not running, starting them...\n")
		if err := orch.Start(instance); err != nil {
			return fmt.Errorf("failed to start containers: %w", err)
		}
	}
	return nil
}

// ImportDatabase copies a SQL dump into the db container and imports it.
func ImportDatabase(orch Orchestrator, databaseName, databasePath, dbPassword string, instance config.Instance) error {
	dbUser := "root"
	if dbPassword == "" {
		dbPassword = "mediawiki"
	}

	quotedPassword := ShellQuote(dbPassword)
	quotedDBName := ShellQuote(databaseName)

	isCompressed := strings.HasSuffix(databasePath, ".sql.gz")

	var containerFile string
	if isCompressed {
		containerFile = fmt.Sprintf("/tmp/%s.sql.gz", databaseName)
	} else {
		containerFile = fmt.Sprintf("/tmp/%s.sql", databaseName)
	}

	err := orch.CopyTo(instance.Path, ServiceDB, databasePath, containerFile)
	if err != nil {
		return fmt.Errorf("error copying database file to container: %w", err)
	}

	defer func() {
		rmCmdStr := fmt.Sprintf("rm -f /tmp/%s.sql /tmp/%s.sql.gz", databaseName, databaseName)
		_, _ = orch.ExecWithError(instance.Path, ServiceDB, rmCmdStr)
	}()

	if isCompressed {
		decompressCmd := fmt.Sprintf("gunzip -f %s", containerFile)
		_, err = orch.ExecWithError(instance.Path, ServiceDB, decompressCmd)
		if err != nil {
			return fmt.Errorf("error decompressing database file: %w", err)
		}
	}

	createCmdStr := fmt.Sprintf("mariadb --no-defaults -u%s -p%s -e 'CREATE DATABASE IF NOT EXISTS %s'", dbUser, quotedPassword, quotedDBName)
	_, err = orch.ExecWithError(instance.Path, ServiceDB, createCmdStr)
	if err != nil {
		return fmt.Errorf("error creating database: %w", err)
	}

	importCmdStr := fmt.Sprintf("mariadb --no-defaults -u%s -p%s %s < /tmp/%s.sql", dbUser, quotedPassword, quotedDBName, databaseName)
	_, err = orch.ExecWithError(instance.Path, ServiceDB, importCmdStr)
	if err != nil {
		return fmt.Errorf("error importing database: %w", err)
	}

	return nil
}
