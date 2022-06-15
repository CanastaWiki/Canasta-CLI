package orchestrators

import (
	"fmt"
	"os/exec"
)

func GetRepoLink(orchestrator string) (string, error) {
	var repo string
	switch orchestrator {
	case "docker-compose":
		repo = "https://github.com/CanastaWiki/Canasta-DockerCompose.git"
	default:
		return repo, fmt.Errorf("orchestrator: %s is not available", orchestrator)
	}

	return repo, nil
}

func Start(path, orchestrator string) error {
	switch orchestrator {
	case "docker-compose":
		fmt.Println("docker compose up")
		cmd := exec.Command("docker-compose", "up", "-d")
		cmd.Dir = path
		out, err := cmd.CombinedOutput()
		fmt.Println(string(out))
		if err != nil {
			return err
		}
	default:
		return fmt.Errorf("orchestrator: %s is not available", orchestrator)
	}

	return nil
}

func Stop(path, orchestrator string) error {
	switch orchestrator {
	case "docker-compose":
		fmt.Println("docker compose down")
		cmd := exec.Command("docker-compose", "down")
		cmd.Dir = path
		out, err := cmd.CombinedOutput()
		fmt.Println(string(out))
		if err != nil {
			return err
		}
	default:
		return fmt.Errorf("orchestrator: %s is not available", orchestrator)
	}

	return nil
}

func StopAndStart(path, orchestrator string) error {
	err := Stop(path, orchestrator)
	if err != nil {
		return err
	}
	err = Start(path, orchestrator)

	return err
}

func Exec(path, orchestrator, container, command string) error {
	switch orchestrator {
	case "docker-compose":
		fmt.Println("docker compose exec " + command)
		cmd := exec.Command("docker-compose", "exec", container, "/bin/bash", "-c", command)
		cmd.Dir = path
		out, err := cmd.CombinedOutput()
		fmt.Println(string(out))
		if err != nil {
			return err
		}
	default:
		return fmt.Errorf("orchestrator: %s is not available", orchestrator)
	}

	return nil
}
