package orchestrators

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

func GetRepoLink(orchestrator string) string {
	var repo string
	switch orchestrator {
	case "docker-compose":
		repo = "https://github.com/CanastaWiki/Canasta-DockerCompose.git"
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return repo
}

func Start(path, orchestrator string) error {
	logging.Print("Starting Canasta\n")
	switch orchestrator {
	case "docker-compose":
		execute.Run(path, "docker-compose", "up", "-d")
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return nil
}

func Stop(path, orchestrator string) error {
	logging.Print("Stoping the containers\n")
	switch orchestrator {
	case "docker-compose":
		execute.Run(path, "docker-compose", "down")
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return nil
}

func StopAndStart(path, orchestrator string) error {
	if err := Stop(path, orchestrator); err != nil {
		logging.Fatal(err)
	}
	if err := Start(path, orchestrator); err != nil {
		logging.Fatal(err)
	}
	return nil
}

func Delete(path, orchestrator string) {
	switch orchestrator {
	case "docker-compose":
		execute.Run(path, "docker-compose", "down", "-v")
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}

	//Deleting the installation folder
	execute.Run("", "rm", "-rf", path)
}

func Exec(path, orchestrator, container, command string) string {
	var output string
	switch orchestrator {
	case "docker-compose":
		output = execute.Run(path, "docker-compose", "exec", "-T", container, "/bin/bash", "-c", command)
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return output
}
