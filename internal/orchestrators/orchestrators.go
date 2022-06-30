package orchestrators

import (
	"fmt"
	"os/exec"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

func GetRepoLink(orchestrator string) (string, error) {
	var repo string
	switch orchestrator {
	case "docker-compose":
		repo = "https://github.com/CanastaWiki/Canasta-DockerCompose.git"
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}

	return repo, nil
}

func Start(path, orchestrator string) error {
	logging.Print("Starting Canasta\n")
	switch orchestrator {
	case "docker-compose":
		logging.Print("docker compose up\n")
		cmd := exec.Command("docker-compose", "up", "-d")
		cmd.Dir = path
		if _, err := cmd.CombinedOutput(); err != nil {
			logging.Fatal(err)
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}

	return nil
}

func Stop(path, orchestrator string) error {
	logging.Print("Stoping the containers\n")
	switch orchestrator {
	case "docker-compose":
		logging.Print("docker compose down\n")
		cmd := exec.Command("docker-compose", "down")
		cmd.Dir = path
		if _, err := cmd.CombinedOutput(); err != nil {
			logging.Fatal(err)
		}
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

func Delete(path, orchestrator string) error {
	switch orchestrator {
	case "docker-compose":
		logging.Print("docker compose down -v")
		cmd := exec.Command("docker-compose", "down", "-v")
		cmd.Dir = path
		if _, err := cmd.CombinedOutput(); err != nil {
			logging.Fatal(err)
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return nil
}

func Exec(path, orchestrator, container, command string) error {
	switch orchestrator {
	case "docker-compose":
		logging.Print(fmt.Sprintf("docker compose exec %s %s ", container, command))
		cmd := exec.Command("docker-compose", "exec", container, "/bin/bash", "-c", command)
		cmd.Dir = path
		out, err := cmd.CombinedOutput()
		logging.Print(string(out))
		if err != nil {
			return err
		}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestrator))
	}
	return nil
}
