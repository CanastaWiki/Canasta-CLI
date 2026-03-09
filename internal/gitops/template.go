package gitops

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

var placeholderRe = regexp.MustCompile(`\{\{(\w+)\}\}`)

// RenderTemplate replaces all {{key}} placeholders in templateContent with
// values from vars. Returns an error listing any placeholders that have no
// corresponding key in vars.
func RenderTemplate(templateContent string, vars VarsMap) (string, error) {
	var missing []string
	seen := make(map[string]bool)

	result := placeholderRe.ReplaceAllStringFunc(templateContent, func(match string) string {
		key := placeholderRe.FindStringSubmatch(match)[1]
		if val, ok := vars[key]; ok {
			return val
		}
		if !seen[key] {
			missing = append(missing, key)
			seen[key] = true
		}
		return match
	})

	if len(missing) > 0 {
		sort.Strings(missing)
		return "", fmt.Errorf("missing keys in vars.yaml: %s", strings.Join(missing, ", "))
	}
	return result, nil
}

// placeholderName converts an .env key name to its placeholder variable name.
// For example, "MYSQL_PASSWORD" becomes "mysql_password".
func placeholderName(envKey string) string {
	return strings.ToLower(envKey)
}

// ExtractTemplate converts the contents of a .env file into an env.template
// and a VarsMap. Keys listed in placeholderKeys have their values replaced
// with {{placeholder}} syntax. Keys matching any of the builtinSecretPrefixes
// are also treated as placeholders automatically. All other keys are kept as
// literals.
//
// The returned VarsMap contains the placeholder-name → original-value mapping.
func ExtractTemplate(envContent string, placeholderKeys []string) (template string, vars VarsMap) {
	keySet := make(map[string]bool, len(placeholderKeys))
	for _, k := range placeholderKeys {
		keySet[k] = true
	}

	vars = make(VarsMap)
	var lines []string

	for _, line := range strings.Split(envContent, "\n") {
		trimmed := strings.TrimSpace(line)

		// Preserve blank lines and comments as-is.
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			lines = append(lines, line)
			continue
		}

		eqIdx := strings.Index(trimmed, "=")
		if eqIdx < 0 {
			lines = append(lines, line)
			continue
		}

		key := trimmed[:eqIdx]
		value := trimmed[eqIdx+1:]

		if keySet[key] || hasSecretPrefix(key) {
			phName := placeholderName(key)
			vars[phName] = stripQuotes(value)
			lines = append(lines, key+"={{"+phName+"}}")
		} else {
			lines = append(lines, line)
		}
	}

	template = strings.Join(lines, "\n")
	return template, vars
}

// hasSecretPrefix returns true if the key matches any of the
// builtinSecretPrefixes (e.g., AWS_, AZURE_, B2_).
func hasSecretPrefix(key string) bool {
	for _, prefix := range builtinSecretPrefixes {
		if strings.HasPrefix(key, prefix) {
			return true
		}
	}
	return false
}

// stripQuotes removes surrounding double quotes from a value, matching the
// behavior of canasta.GetEnvVariable.
func stripQuotes(s string) string {
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		return s[1 : len(s)-1]
	}
	return s
}

// FindMissingCustomKeys returns custom key names whose placeholder equivalents
// are not present in the vars map (i.e., they were not found in .env).
func FindMissingCustomKeys(customKeys []string, vars VarsMap) []string {
	var missing []string
	for _, key := range customKeys {
		if _, ok := vars[placeholderName(key)]; !ok {
			missing = append(missing, key)
		}
	}
	return missing
}

// AllPlaceholderKeys returns the combined list of built-in secret keys,
// built-in host keys, and any custom keys.
func AllPlaceholderKeys(customKeys []string) []string {
	keys := make([]string, 0, len(builtinSecretKeys)+len(builtinHostKeys)+len(customKeys))
	keys = append(keys, builtinSecretKeys...)
	keys = append(keys, builtinHostKeys...)
	keys = append(keys, customKeys...)
	return keys
}
