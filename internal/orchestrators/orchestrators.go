package orchestrators

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

const (
	devComposeFile      = "docker-compose.dev.yml"
	mainComposeFile     = "docker-compose.yml"
	overrideComposeFile = "docker-compose.override.yml"
)

func CheckDependencies() {
	compose := config.GetOrchestrator("compose")
	if compose.Path != "" {
		cmd := exec.Command(compose.Path, "version")
		err := cmd.Run()
		if err != nil {
			logging.Fatal(fmt.Errorf("unable to execute compose (%s) \n", err))
		}
	} else {
		cmd := exec.Command("docker", "compose", "version")
		err := cmd.Run()
		if err != nil {
			logging.Fatal(fmt.Errorf("docker compose should be installed! (%s) \n", err))
		}
	}
}

// GetDevComposeFiles returns the list of compose files needed for dev mode
// It checks if docker-compose.override.yml exists and includes it
func GetDevComposeFiles(installPath string) []string {
	files := []string{mainComposeFile}

	// Include override file if it exists (contains port mappings)
	overridePath := filepath.Join(installPath, overrideComposeFile)
	if _, err := os.Stat(overridePath); err == nil {
		files = append(files, overrideComposeFile)
	}

	// Dev compose file goes last to override settings
	files = append(files, devComposeFile)
	return files
}

func GetRepoLink(orchestrator string) string {
	var repo string
	switch orchestrator {
	case "docker-compose":
		repo = "https://github.com/CanastaWiki/Canasta-DockerCompose.git"
	case "compose":
		repo = "https://github.com/CanastaWiki/Canasta-DockerCompose.git"
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return repo
}

func CopyOverrideFile(installPath, orchestrator, sourceFilename, workingDir string) error {
	if sourceFilename != "" {
		logging.Print("Copying override file\n")
		switch orchestrator {
		case "compose":
			if !strings.HasPrefix(sourceFilename, "/") {
				sourceFilename = workingDir + "/" + sourceFilename
			}
			var overrideFilename = installPath + "/docker-compose.override.yml"
			logging.Print(fmt.Sprintf("Copying %s to %s\n", sourceFilename, overrideFilename))
			err, output := execute.Run("", "cp", sourceFilename, overrideFilename)
			if err != nil {
				logging.Fatal(fmt.Errorf(output))
			}
		default:
			logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
		}
	}
	return nil
}

// Start starts containers, automatically using dev mode compose files if DevMode is enabled
func Start(instance config.Installation) error {
	var files []string
	if instance.DevMode {
		fmt.Println("Dev mode enabled (Xdebug active)")
		files = GetDevComposeFiles(instance.Path)
	} else {
		logging.Print("Starting Canasta\n")
	}

	switch instance.Orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var args []string
		for _, f := range files {
			args = append(args, "-f", f)
		}
		args = append(args, "up", "-d")
		if compose.Path != "" {
			err, output := execute.Run(instance.Path, compose.Path, args...)
			if err != nil {
				return fmt.Errorf(output)
			}
		} else {
			allArgs := append([]string{"compose"}, args...)
			err, output := execute.Run(instance.Path, "docker", allArgs...)
			if err != nil {
				return fmt.Errorf(output)
			}
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", instance.Orchestrator))
	}
	return nil
}

func Pull(installPath, orchestrator string) error {
	logging.Print("Pulling Canasta image\n")
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		if compose.Path != "" {
			err, output := execute.Run(installPath, compose.Path, "pull", "--ignore-buildable", "--ignore-pull-failures")
			if err != nil {
				return fmt.Errorf(output)
			}
		} else {
			err, output := execute.Run(installPath, "docker", "compose", "pull", "--ignore-buildable", "--ignore-pull-failures")
			if err != nil {
				return fmt.Errorf(output)
			}
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return nil
}


// Stop stops containers, automatically using dev mode compose files if DevMode is enabled
func Stop(instance config.Installation) error {
	var files []string
	if instance.DevMode {
		logging.Print("Stopping Canasta (dev mode)\n")
		files = GetDevComposeFiles(instance.Path)
	} else {
		logging.Print("Stopping the containers\n")
	}

	switch instance.Orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var args []string
		for _, f := range files {
			args = append(args, "-f", f)
		}
		args = append(args, "down")
		if compose.Path != "" {
			err, output := execute.Run(instance.Path, compose.Path, args...)
			if err != nil {
				return fmt.Errorf(output)
			}
		} else {
			allArgs := append([]string{"compose"}, args...)
			err, output := execute.Run(instance.Path, "docker", allArgs...)
			if err != nil {
				return fmt.Errorf(output)
			}
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", instance.Orchestrator))
	}
	return nil
}

// Build builds images using the specified compose files
func Build(installPath, orchestrator string, files ...string) error {
	logging.Print("Building images\n")
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var args []string
		for _, f := range files {
			args = append(args, "-f", f)
		}
		args = append(args, "build")
		if compose.Path != "" {
			err, output := execute.Run(installPath, compose.Path, args...)
			if err != nil {
				return fmt.Errorf(output)
			}
		} else {
			allArgs := append([]string{"compose"}, args...)
			err, output := execute.Run(installPath, "docker", allArgs...)
			if err != nil {
				return fmt.Errorf(output)
			}
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return nil
}

// StopAndStart stops and starts containers, respecting dev mode setting
func StopAndStart(instance config.Installation) error {
	if err := Stop(instance); err != nil {
		return err
	}
	if err := Start(instance); err != nil {
		return err
	}
	return nil
}

// CleanupImages removes images from inside the container before host-side deletion.
// This is necessary on Linux where container-created files (owned by www-data) retain
// their container UID on the host and cannot be deleted without sudo.
// If wikiID is empty, removes all images (images/*). Otherwise removes images/{wikiID}.
func CleanupImages(installPath, orchestrator, wikiID string) error {
	var cleanupCmd string
	if wikiID == "" {
		// Use find to delete all files including hidden files (dotfiles)
		// The glob * doesn't match dotfiles like .htaccess
		cleanupCmd = "find /mediawiki/images -mindepth 1 -delete"
	} else {
		cleanupCmd = fmt.Sprintf("rm -rf /mediawiki/images/%s", wikiID)
	}
	_, err := ExecWithError(installPath, orchestrator, "web", cleanupCmd)
	return err
}

func DeleteContainers(installPath, orchestrator string) (string, error) {
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		if compose.Path != "" {

			err, output := execute.Run(installPath, compose.Path, "down", "-v")
			return output, err
		} else {
			err, output := execute.Run(installPath, "docker", "compose", "down", "-v")
			return output, err
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return "", nil
}

func DeleteConfig(installPath string) (string, error) {
	//Deleting the installation folder
	err, output := execute.Run("", "rm", "-rf", installPath)
	return output, err
}

func ExecWithError(installPath, orchestrator, container, command string) (string, error) {
	var outputByte []byte
	var err error

	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		if compose.Path != "" {

			cmd := exec.Command(compose.Path, "exec", "-T", container, "/bin/bash", "-c", command)
			if installPath != "" {
				cmd.Dir = installPath
			}
			outputByte, err = cmd.CombinedOutput()
		} else {
			cmd := exec.Command("docker", "compose", "exec", "-T", container, "/bin/bash", "-c", command)
			if installPath != "" {
				cmd.Dir = installPath
			}
			outputByte, err = cmd.CombinedOutput()
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	output := string(outputByte)
	logging.Print(output)
	return output, err
}

func Exec(installPath, orchestrator, container, command string) string {
	output, err := ExecWithError(installPath, orchestrator, container, command)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	}
	return output

}

// ExecStreaming runs a command in a container with stdout/stderr piped
// directly to the terminal, providing real-time output for long-running commands.
func ExecStreaming(installPath, orchestrator, container, command string) error {
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var cmd *exec.Cmd
		if compose.Path != "" {
			cmd = exec.Command(compose.Path, "exec", "-T", container, "/bin/bash", "-c", command)
		} else {
			cmd = exec.Command("docker", "compose", "exec", "-T", container, "/bin/bash", "-c", command)
		}
		if installPath != "" {
			cmd.Dir = installPath
		}
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("command failed: %w", err)
		}
	default:
		return fmt.Errorf("orchestrator: %s is not available", orchestrator)
	}
	return nil
}

// CheckRunningStatus checks if the web container is running
func CheckRunningStatus(instance config.Installation) error {
	containerName := "web"

	switch instance.Orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var output string
		var err error
		if compose.Path != "" {
			err, output = execute.Run(instance.Path, compose.Path, "ps", "-q", containerName)
		} else {
			err, output = execute.Run(instance.Path, "docker", "compose", "ps", "-q", containerName)
		}
		if err != nil || output == "" {
			return fmt.Errorf("Container %s is not running", containerName)
		}
	default:
		return fmt.Errorf("Orchestrator: %s is not available", instance.Orchestrator)
	}
	return nil
}

// EnsureRunning checks if containers are running and starts them if not.
// Returns nil if containers are running (or were successfully started).
func EnsureRunning(instance config.Installation) error {
	if err := CheckRunningStatus(instance); err != nil {
		logging.Print("Containers not running, starting them...\n")
		if err := Start(instance); err != nil {
			return fmt.Errorf("failed to start containers: %w", err)
		}
	}
	return nil
}

func ImportDatabase(databaseName, databasePath, dbPassword string, instance config.Installation) error {
	dbUser := "root"
	if dbPassword == "" {
		dbPassword = "mediawiki" // default password
	}

	// Escape single quotes in password for shell safety (replace ' with '\'' for bash single-quoted strings)
	escapedPassword := strings.ReplaceAll(dbPassword, "'", "'\\''")

	// Determine if the file is compressed
	isCompressed := strings.HasSuffix(databasePath, ".sql.gz")

	// Determine the filename in the container
	var containerFile string
	if isCompressed {
		containerFile = fmt.Sprintf("/tmp/%s.sql.gz", databaseName)
	} else {
		containerFile = fmt.Sprintf("/tmp/%s.sql", databaseName)
	}

	// Copy the database file into the db container using docker compose cp
	err := CopyToContainer(instance.Path, instance.Orchestrator, "db", databasePath, containerFile)
	if err != nil {
		return fmt.Errorf("error copying database file to container: %w", err)
	}

	// Ensure the temporary file is removed after the function returns
	defer func() {
		rmCmdStr := fmt.Sprintf("rm -f /tmp/%s.sql /tmp/%s.sql.gz", databaseName, databaseName)
		_, _ = ExecWithError(instance.Path, instance.Orchestrator, "db", rmCmdStr)
	}()

	// If compressed, decompress the file
	if isCompressed {
		decompressCmd := fmt.Sprintf("gunzip -f %s", containerFile)
		_, err = ExecWithError(instance.Path, instance.Orchestrator, "db", decompressCmd)
		if err != nil {
			return fmt.Errorf("error decompressing database file: %w", err)
		}
	}

	// Run the mysql command to create the new database
	// Use single quotes around password to prevent shell interpretation of special characters
	createCmdStr := fmt.Sprintf("mysql --no-defaults -u%s -p'%s' -e 'CREATE DATABASE IF NOT EXISTS %s'", dbUser, escapedPassword, databaseName)
	_, err = ExecWithError(instance.Path, instance.Orchestrator, "db", createCmdStr)
	if err != nil {
		return fmt.Errorf("error creating database: %w", err)
	}

	// Run the mysql command to import the .sql file into the new database
	importCmdStr := fmt.Sprintf("mysql --no-defaults -u%s -p'%s' %s < /tmp/%s.sql", dbUser, escapedPassword, databaseName, databaseName)
	_, err = ExecWithError(instance.Path, instance.Orchestrator, "db", importCmdStr)
	if err != nil {
		return fmt.Errorf("error importing database: %w", err)
	}

	return nil
}

// CopyFromContainer copies a file from a container to the host
func CopyFromContainer(installPath, orchestrator, container, containerPath, hostPath string) error {
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var cmd *exec.Cmd
		if compose.Path != "" {
			cmd = exec.Command(compose.Path, "cp", container+":"+containerPath, hostPath)
		} else {
			cmd = exec.Command("docker", "compose", "cp", container+":"+containerPath, hostPath)
		}
		if installPath != "" {
			cmd.Dir = installPath
		}
		outputByte, err := cmd.CombinedOutput()
		if err != nil {
			return fmt.Errorf("failed to copy from container: %s - %w", string(outputByte), err)
		}
		return nil
	default:
		return fmt.Errorf("orchestrator: %s is not available", orchestrator)
	}
}

// CopyToContainer copies a file from the host to a container
func CopyToContainer(installPath, orchestrator, container, hostPath, containerPath string) error {
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var cmd *exec.Cmd
		if compose.Path != "" {
			cmd = exec.Command(compose.Path, "cp", hostPath, container+":"+containerPath)
		} else {
			cmd = exec.Command("docker", "compose", "cp", hostPath, container+":"+containerPath)
		}
		if installPath != "" {
			cmd.Dir = installPath
		}
		outputByte, err := cmd.CombinedOutput()
		if err != nil {
			return fmt.Errorf("failed to copy to container: %s - %w", string(outputByte), err)
		}
		return nil
	default:
		return fmt.Errorf("orchestrator: %s is not available", orchestrator)
	}
}

