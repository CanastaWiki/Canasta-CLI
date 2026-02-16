package orchestrators

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

// RunRestic runs a restic command using the appropriate method for the orchestrator.
// For Compose: runs restic via docker run with optional volume mounts.
// volumes maps host paths to container paths (e.g., {"/host/path": "/container/path"}).
func RunRestic(orch Orchestrator, installPath, envPath string, volumes map[string]string, args ...string) (string, error) {
	switch orch.(type) {
	case *ComposeOrchestrator:
		return runResticCompose(installPath, envPath, volumes, args...)
	default:
		return "", fmt.Errorf("restic not supported for this orchestrator type")
	}
}

func runResticCompose(installPath, envPath string, volumes map[string]string, args ...string) (string, error) {
	cmdArgs := []string{"docker", "run", "--rm", "-i", "--env-file", envPath}
	for hostPath, containerPath := range volumes {
		cmdArgs = append(cmdArgs, "-v", hostPath+":"+containerPath)
	}
	cmdArgs = append(cmdArgs, "restic/restic")
	cmdArgs = append(cmdArgs, args...)

	err, output := execute.Run(installPath, "sudo", cmdArgs...)
	if err != nil {
		return output, fmt.Errorf("restic command failed: %s", output)
	}
	return output, nil
}
