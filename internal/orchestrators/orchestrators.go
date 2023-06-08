package orchestrators

import (
	"fmt"
	"os/exec"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

func CheckDependencies() {
	_, err := exec.LookPath("docker-compose")
	if err != nil {
		logging.Fatal(fmt.Errorf("docker-compose should be installed! (%s)", err))
	}
}

func GetRepoLink(orchestrator string) string {
	var repo string
	switch orchestrator {
	case "docker-compose":
		repo = "https://github.com/chl178/Canasta-DockerCompose.git"
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return repo
}

func Start(path, orchestrator string) error {
	logging.Print("Starting Canasta\n")
	switch orchestrator {
	case "docker-compose":
		err, output := execute.Run(path, "docker-compose", "up", "-d")
		if err != nil {
			return fmt.Errorf(output)
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return nil
}

func Stop(path, orchestrator string) error {
	logging.Print("Stopping the containers\n")
	switch orchestrator {
	case "docker-compose":
		err, output := execute.Run(path, "docker-compose", "down")
		if err != nil {
			return fmt.Errorf(output)
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return nil
}

func StopAndStart(path, orchestrator string) error {
	if err := Stop(path, orchestrator); err != nil {
		return err
	}
	if err := Start(path, orchestrator); err != nil {
		return err
	}
	return nil
}

func DeleteContainers(path, orchestrator string) (string, error) {
	switch orchestrator {
	case "docker-compose":
		err, output := execute.Run(path, "docker-compose", "down", "-v")
		return output, err
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return "", nil
}

func DeleteConfig(path string) (string, error) {
	//Deleting the installation folder
	err, output := execute.Run("", "rm", "-rf", path)
	return output, err
}

func ExecWithError(path, orchestrator, container, command string) (string, error) {
	var outputByte []byte
	var err error

	switch orchestrator {
	case "docker-compose":
		cmd := exec.Command("docker-compose", "exec", "-T", container, "/bin/bash", "-c", command)
		if path != "" {
			cmd.Dir = path
		}
		outputByte, err = cmd.CombinedOutput()
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	output := string(outputByte)
	logging.Print(output)
	return output, err
}

func Exec(path, orchestrator, container, command string) string {
	output, err := ExecWithError(path, orchestrator, container, command)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	}
	return output

}

func CheckRunningStatus(path, id string) error {
	containerName := id + "_web_1"
	
	command := fmt.Sprintf("docker ps --filter name=%s", containerName)
	err, output := execute.Run(path, command)
	
	if err != nil {
		logging.Fatal(err)
		return err
	}
	
	if !strings.Contains(output, containerName) {
		logging.Fatal(fmt.Errorf("Container %s is not running, please start it first if you want to add a new wiki!", containerName))
		return fmt.Errorf("Container %s is not running", containerName)
	}
	
	return nil
}