package gitops

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	"gopkg.in/yaml.v2"
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

// wikiURLKey returns the vars key for a wiki's URL placeholder.
func wikiURLKey(wikiID string) string {
	return "wiki_url_" + wikiID
}

// wikisYAML is a minimal representation of wikis.yaml used only for
// template extraction and rendering. We intentionally keep this
// separate from farmsettings.Wikis to avoid a circular import.
type wikisYAML struct {
	Wikis []wikiEntry `yaml:"wikis"`
}

type wikiEntry struct {
	ID   string `yaml:"id"`
	URL  string `yaml:"url"`
	NAME string `yaml:"name"`
}

// ExtractWikisTemplate converts wikis.yaml content into a template where
// each wiki's URL is replaced with a {{wiki_url_<id>}} placeholder.
// Returns the template content and a VarsMap with the original URL values.
func ExtractWikisTemplate(wikisContent string) (string, VarsMap, error) {
	var w wikisYAML
	if err := yaml.Unmarshal([]byte(wikisContent), &w); err != nil {
		return "", nil, fmt.Errorf("parsing wikis.yaml: %w", err)
	}

	vars := make(VarsMap, len(w.Wikis))
	for i, wiki := range w.Wikis {
		key := wikiURLKey(wiki.ID)
		vars[key] = wiki.URL
		w.Wikis[i].URL = "{{" + key + "}}"
	}

	out, err := yaml.Marshal(&w)
	if err != nil {
		return "", nil, fmt.Errorf("marshaling wikis template: %w", err)
	}
	return string(out), vars, nil
}

// RenderWikisTemplate renders a wikis.yaml.template by replacing
// {{wiki_url_<id>}} placeholders with values from vars.
func RenderWikisTemplate(templateContent string, vars VarsMap) (string, error) {
	return RenderTemplate(templateContent, vars)
}

// SyncWikisTemplate regenerates wikis.yaml.template from the current
// config/wikis.yaml and updates the current host's vars with any new or
// removed wiki_url_* entries. This is a no-op if gitops is not active
// (i.e., wikis.yaml.template does not exist).
func SyncWikisTemplate(installPath string) error {
	// Check if gitops is active by looking for the template file.
	existingTemplate, err := LoadWikisTemplate(installPath)
	if err != nil {
		return err
	}
	if existingTemplate == "" {
		return nil
	}

	// Read the current wikis.yaml.
	wikisContent, err := LoadWikisYAML(installPath)
	if err != nil {
		return err
	}
	if wikisContent == "" {
		return nil
	}

	// Regenerate the template.
	newTemplate, newVars, err := ExtractWikisTemplate(wikisContent)
	if err != nil {
		return err
	}
	if err := SaveWikisTemplate(installPath, newTemplate); err != nil {
		return err
	}

	// Update the current host's vars with the new wiki URL entries.
	hostName, err := LoadLocalHost(installPath)
	if err != nil || hostName == "" {
		// No host configured — nothing more to do.
		return nil
	}

	vars, err := LoadVars(installPath, hostName)
	if err != nil {
		return err
	}

	// Remove old wiki_url_* entries that are no longer in the template.
	for key := range vars {
		if strings.HasPrefix(key, "wiki_url_") {
			if _, ok := newVars[key]; !ok {
				delete(vars, key)
			}
		}
	}

	// Add new wiki_url_* entries that are not yet in the vars.
	for key, value := range newVars {
		if _, ok := vars[key]; !ok {
			vars[key] = value
		}
	}

	return SaveVars(installPath, hostName, vars)
}
