package orchestrators

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

// backupVolumeName returns the persistent Docker volume name for an installation.
func backupVolumeName(installPath string) string {
	return "canasta-backup-" + filepath.Base(installPath)
}

// isLocalRepo returns true if the repository URL is a local filesystem path
// rather than a remote backend (s3:, sftp:, rest:, gs:, azure:, b2:, rclone:).
func isLocalRepo(repoURL string) bool {
	return strings.HasPrefix(repoURL, "/")
}

// repoFromArgs extracts the repository URL following the -r flag in args.
func repoFromArgs(args []string) string {
	for i, arg := range args {
		if arg == "-r" && i+1 < len(args) {
			return args[i+1]
		}
	}
	return ""
}

// stageToVolume copies host directories into the backup volume via an alpine container.
func stageToVolume(volName string, volumes map[string]string) error {
	cmdArgs := []string{"docker", "run", "--rm",
		"-v", volName + ":/currentsnapshot",
	}

	var copyParts []string
	i := 0
	for hostPath, containerPath := range volumes {
		mountPoint := fmt.Sprintf("/src%d", i)
		cmdArgs = append(cmdArgs, "-v", hostPath+":"+mountPoint+":ro")
		copyParts = append(copyParts, fmt.Sprintf("cp -a %s %s", mountPoint, containerPath))
		i++
	}

	shellCmd := "rm -rf /currentsnapshot/* && " + strings.Join(copyParts, " && ")
	cmdArgs = append(cmdArgs, "alpine", "sh", "-c", "'"+shellCmd+"'")

	err, output := execute.Run("", cmdArgs[0], cmdArgs[1:]...)
	if err != nil {
		return fmt.Errorf("failed to stage files to backup volume: %s", output)
	}
	return nil
}

// runResticDocker runs a restic command inside a Docker container with the
// backup volume mounted at /currentsnapshot. The envPath file provides
// environment variables (RESTIC_REPOSITORY, RESTIC_PASSWORD, etc.).
func runResticDocker(installPath, envPath, volName string, args ...string) (string, error) {
	cmdArgs := []string{"docker", "run", "--rm", "-i", "--env-file", envPath,
		"-v", volName + ":/currentsnapshot",
	}

	cmdArgs = append(cmdArgs, "restic/restic")
	cmdArgs = append(cmdArgs, args...)

	err, output := execute.Run(installPath, cmdArgs[0], cmdArgs[1:]...)
	if err != nil {
		if strings.Contains(output, "repository does not exist") {
			return output, fmt.Errorf("backup repository not found. Run 'canasta backup init' to create it")
		}
		return output, fmt.Errorf("restic command failed: %s", output)
	}
	return output, nil
}

// restoreFromVolume copies data from the backup Docker volume to host
// directories. Each entry in dirs maps a volume path (e.g.
// "/currentsnapshot/config") to a host path.
func restoreFromVolume(volName, installPath string, dirs map[string]string) error {
	cmdArgs := []string{"docker", "run", "--rm",
		"-v", volName + ":/currentsnapshot:ro",
		"-v", installPath + ":/install",
	}

	var copyParts []string
	for volumePath, hostPath := range dirs {
		relPath, err := filepath.Rel(installPath, hostPath)
		if err != nil {
			return fmt.Errorf("failed to compute relative path for %s: %w", hostPath, err)
		}
		dst := "/install/" + relPath
		// Handle both directories and individual files.
		// For directories, clear contents without removing the directory itself
		// to preserve active Docker bind mounts.
		copyParts = append(copyParts,
			fmt.Sprintf("if [ -d %s ]; then mkdir -p %s && rm -rf %s/* %s/.[!.]* 2>/dev/null; cp -a %s/. %s/; elif [ -f %s ]; then cp -a %s %s; fi",
				volumePath, dst, dst, dst, volumePath, dst, volumePath, volumePath, dst))
	}

	shellCmd := strings.Join(copyParts, " && ")
	cmdArgs = append(cmdArgs, "alpine", "sh", "-c", "'"+shellCmd+"'")

	err, output := execute.Run("", cmdArgs[0], cmdArgs[1:]...)
	if err != nil {
		return fmt.Errorf("failed to restore files from backup volume: %s", output)
	}
	return nil
}
