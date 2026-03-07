package gitops

import (
	"path/filepath"
	"strings"
)

// AnalyzeChanges examines a list of changed file paths and determines
// whether a restart or maintenance update is needed.
func AnalyzeChanges(changedFiles []string) (needsRestart, needsMaintenance bool, submodulesUpdated []string) {
	seen := make(map[string]bool)
	for _, f := range changedFiles {
		// Normalize path separators.
		f = filepath.ToSlash(f)

		switch {
		case f == "env.template" || f == ".env":
			needsRestart = true
		case f == "config/wikis.yaml":
			needsRestart = true
		case f == "config/Caddyfile.site" || f == "config/Caddyfile.global":
			needsRestart = true
		case f == "docker-compose.override.yml":
			needsRestart = true
		case strings.HasPrefix(f, "extensions/") || strings.HasPrefix(f, "skins/"):
			parts := strings.SplitN(f, "/", 3)
			if len(parts) >= 2 {
				submod := parts[0] + "/" + parts[1]
				if !seen[submod] {
					submodulesUpdated = append(submodulesUpdated, submod)
					seen[submod] = true
					needsMaintenance = true
				}
			}
		}
	}
	return needsRestart, needsMaintenance, submodulesUpdated
}

// CanPush returns true if the host's role allows pushing.
func CanPush(role string) bool {
	return role == RoleSource || role == RoleBoth
}

// CanPull returns true if the host's role allows pulling.
func CanPull(role string) bool {
	return role == RoleSink || role == RoleBoth
}
