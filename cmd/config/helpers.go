package config

import "strings"

// resolveKey finds the actual key in envVars using case-insensitive matching
// with hyphens treated as underscores. If no match is found, returns the
// input uppercased with hyphens replaced by underscores.
func resolveKey(envVars map[string]string, input string) string {
	normalized := strings.ReplaceAll(input, "-", "_")
	for k := range envVars {
		if strings.EqualFold(k, normalized) {
			return k
		}
	}
	return strings.ToUpper(normalized)
}

// isKnownKey reports whether key is in the knownKeys set or matches a
// Restic backend prefix.
func isKnownKey(key string) bool {
	if knownKeys[key] {
		return true
	}
	for _, prefix := range resticPrefixes {
		if strings.HasPrefix(key, prefix) {
			return true
		}
	}
	return false
}
