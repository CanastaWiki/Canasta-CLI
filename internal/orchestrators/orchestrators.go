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
	if instance.DevMode {
		logging.Print("Starting Canasta in dev mode\n")
		files := GetDevComposeFiles(instance.Path)
		return start(instance.Path, instance.Orchestrator, files...)
	}
	return start(instance.Path, instance.Orchestrator)
}

// start is the internal function that starts containers. If files are provided, uses those
// compose files explicitly; otherwise uses default compose file discovery.
func start(installPath, orchestrator string, files ...string) error {
	logging.Print("Starting Canasta\n")
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var args []string
		for _, f := range files {
			args = append(args, "-f", f)
		}
		args = append(args, "up", "-d")
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

func Pull(installPath, orchestrator string) error {
	logging.Print("Pulling Canasta image\n")
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		if compose.Path != "" {
			err, output := execute.Run(installPath, compose.Path, "pull")
			if err != nil {
				return fmt.Errorf(output)
			}
		} else {
			err, output := execute.Run(installPath, "docker", "compose", "pull")
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
	if instance.DevMode {
		logging.Print("Stopping Canasta (dev mode)\n")
		files := GetDevComposeFiles(instance.Path)
		return stop(instance.Path, instance.Orchestrator, files...)
	}
	return stop(instance.Path, instance.Orchestrator)
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

// stop is the internal function that stops containers. If files are provided, uses those
// compose files explicitly; otherwise uses default compose file discovery.
func stop(installPath, orchestrator string, files ...string) error {
	logging.Print("Stopping the containers\n")
	switch orchestrator {
	case "compose":
		compose := config.GetOrchestrator("compose")
		var args []string
		for _, f := range files {
			args = append(args, "-f", f)
		}
		args = append(args, "down")
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
			logging.Fatal(fmt.Errorf("Container %s is not running in Canasta instance '%s', please start it first!", containerName, instance.Id))
			return fmt.Errorf("Container %s is not running", containerName)
		}
	default:
		logging.Fatal(fmt.Errorf("Orchestrator: %s is not available", instance.Orchestrator))
		return fmt.Errorf("Orchestrator: %s is not available", instance.Orchestrator)
	}
	return nil
}

func ExportDatabase(installPath, orchestrator, wikiID, outputFilePath string) error {
	// MySQL user, password and container name
	// Replace with your actual MySQL username and password and MySQL container name
	mysqlUser := "root"
	mysqlPassword := "mediawiki"
	mysqlContainerName := "db" // adjust as per your setup

	// Constructing mysqldump command
	dumpCommand := fmt.Sprintf("mysqldump -u %s -p%s %s > /tmp/%s.sql", mysqlUser, mysqlPassword, wikiID, wikiID)
	// Executing mysqldump command inside the MySQL container
	_, err := ExecWithError(installPath, orchestrator, mysqlContainerName, dumpCommand)
	if err != nil {
		return fmt.Errorf("Failed to execute mysqldump command: %v", err)
	}

	// After executing the mysqldump command, the dump file is inside the container.
	// We need to copy it from the container to the host machine.

	// Constructing docker cp command
	copyCommand := fmt.Sprintf("docker cp %s:/tmp/%s.sql %s", mysqlContainerName, wikiID, outputFilePath)

	// Executing docker cp command on the host machine
	err, output := execute.Run("", "/bin/bash", "-c", copyCommand)
	if err != nil {
		return fmt.Errorf("Failed to copy the dump file from the container: %s", output)
	}

	// Construct the remove command to delete the .sql file from the container
	removeCommand := fmt.Sprintf("rm /tmp/%s.sql", wikiID)

	// Execute the remove command
	_, err = ExecWithError(installPath, orchestrator, mysqlContainerName, removeCommand)
	if err != nil {
		logging.Fatal(fmt.Errorf("Failed to remove .sql file from container: %w", err))
	}

	return nil
}

func ImportDatabase(databaseName, databasePath string, instance config.Installation) error {
	dbUser := "root"
	dbPassword := "mediawiki"

	// Copy the .sql file into the db container
	copyCmdStr := fmt.Sprintf("docker cp %s db:/tmp/%s.sql", databasePath, databaseName)
	_, err := exec.Command("/bin/bash", "-c", copyCmdStr).Output()
	if err != nil {
		return fmt.Errorf("error copying .sql file to container: %w", err)
	}

	// Ensure the temporary .sql file is removed after the function returns
	defer func() {
		rmCmdStr := fmt.Sprintf("rm /tmp/%s.sql", databaseName)
		_, err := ExecWithError(instance.Path, instance.Orchestrator, "db", rmCmdStr)
		if err != nil {
			logging.Fatal(fmt.Errorf("error removing .sql file from container: %w", err))
		}
	}()

	// Run the mysql command to create the new database
	createCmdStr := fmt.Sprintf("mysql -u%s -p%s -e 'CREATE DATABASE IF NOT EXISTS %s'", dbUser, dbPassword, databaseName)
	_, err = ExecWithError(instance.Path, instance.Orchestrator, "db", createCmdStr)
	if err != nil {
		return fmt.Errorf("error creating database: %w", err)
	}

	// Run the mysql command to import the .sql file into the new database
	importCmdStr := fmt.Sprintf("mysql -u%s -p%s %s < /tmp/%s.sql", dbUser, dbPassword, databaseName, databaseName)
	_, err = ExecWithError(instance.Path, instance.Orchestrator, "db", importCmdStr)
	if err != nil {
		return fmt.Errorf("error importing database: %w", err)
	}

	return nil
}

