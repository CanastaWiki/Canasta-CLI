package create

import (
	"fmt"
	"path/filepath"
	"regexp"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
)

// instanceIDPattern matches valid Canasta instance IDs: alphanumeric with
// hyphens and underscores, must start and end with a letter or digit.
var instanceIDPattern = regexp.MustCompile(`^[a-zA-Z0-9]([a-zA-Z0-9-_]*[a-zA-Z0-9])?$`)

// ValidateInstanceID checks that the given ID matches the required format.
func ValidateInstanceID(id string) error {
	if !instanceIDPattern.MatchString(id) {
		return fmt.Errorf("canasta instance ID should not contain spaces or non-ASCII characters, only alphanumeric characters are allowed")
	}
	return nil
}

// ValidateCreateFlags checks that the combination of flags passed to
// `canasta create` is consistent. It validates the wiki ID (when no yaml
// file is given), the instance ID format, mutual exclusivity of image
// flags, and the database path.
func ValidateCreateFlags(wikiID, yamlPath, instanceID, canastaImage, buildFromPath, databasePath string) error {
	if yamlPath == "" {
		if wikiID == "" {
			return fmt.Errorf("--wiki flag is required when --yamlfile is not provided")
		}
		if err := farmsettings.ValidateWikiID(wikiID); err != nil {
			return err
		}
	}

	if err := ValidateInstanceID(instanceID); err != nil {
		return err
	}

	if canastaImage != "" && buildFromPath != "" {
		return fmt.Errorf("--canasta-image and --build-from are mutually exclusive")
	}

	if databasePath != "" {
		if err := canasta.ValidateDatabasePath(databasePath); err != nil {
			return err
		}
	}

	return nil
}

// ResolveFilePaths converts each non-empty relative path to an absolute path
// relative to baseDir.
func ResolveFilePaths(baseDir string, paths ...*string) {
	for _, p := range paths {
		if *p != "" && !filepath.IsAbs(*p) {
			*p = filepath.Join(baseDir, *p)
		}
	}
}

// BuildDomainWithPort appends a non-standard HTTPS port to the domain. If
// envVars contains an HTTPS_PORT that is not "443" and not empty, the port
// is appended with a colon separator.
func BuildDomainWithPort(domain string, envVars map[string]string) string {
	if port, ok := envVars["HTTPS_PORT"]; ok && port != "443" && port != "" {
		return domain + ":" + port
	}
	return domain
}
