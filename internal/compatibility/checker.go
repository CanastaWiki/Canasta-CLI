package compatibility

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI/cmd/version"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
)

// CheckCompatibility checks if the CLI version matches the instance version
// Returns a warning message if there's a mismatch, or empty string if compatible
func CheckCompatibility(instance config.Installation) string {
	currentVersion := version.GetVersion()
	instanceVersion := instance.CliVersion

	// If instance has no version recorded, it was created by an old CLI
	if instanceVersion == "" {
		return fmt.Sprintf(
			"Warning: Instance '%s' was created with an older CLI version that didn't track versions.\n"+
				"Run 'canasta upgrade -i %s' to update it to CLI version %s.",
			instance.Id, instance.Id, currentVersion)
	}

	// If versions don't match, warn user
	if instanceVersion != currentVersion {
		return fmt.Sprintf(
			"Warning: Instance '%s' was created/upgraded with CLI %s, but you're using %s.\n"+
				"Run 'canasta upgrade -i %s' to update it to CLI version %s.",
			instance.Id, instanceVersion, currentVersion, instance.Id, currentVersion)
	}

	return ""
}

// CheckCompatibilityStrict performs the same check but returns an error for mismatches
// This should be used for destructive/modifying commands
func CheckCompatibilityStrict(instance config.Installation) error {
	warning := CheckCompatibility(instance)
	if warning != "" {
		return fmt.Errorf(warning)
	}
	return nil
}

// ShouldWarn returns true if a version warning should be shown for this instance
func ShouldWarn(instance config.Installation) bool {
	return CheckCompatibility(instance) != ""
}
