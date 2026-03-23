package gitops

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v2"

	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
)

const (
	hostsFile         = "hosts.yaml"
	customKeysFile    = "custom-keys.yaml"
	envTemplateFile   = "env.template"
	wikisTemplateFile = "wikis.yaml.template"
	wikisFile         = "config/wikis.yaml"
	appliedFile       = ".gitops-applied"
	hostFile          = ".gitops-host"
)

// LoadHostsConfig reads and parses hosts.yaml from the instance directory.
func LoadHostsConfig(installPath string) (*HostsConfig, error) {
	data, err := os.ReadFile(filepath.Join(installPath, hostsFile))
	if err != nil {
		return nil, fmt.Errorf("reading %s: %w", hostsFile, err)
	}
	var cfg HostsConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", hostsFile, err)
	}
	// Apply default role for single-host configs; validate roles for multi-host.
	if len(cfg.Hosts) == 1 {
		for name, entry := range cfg.Hosts {
			if entry.Role == "" {
				entry.Role = RoleBoth
				cfg.Hosts[name] = entry
			}
		}
	}
	for name, entry := range cfg.Hosts {
		if entry.Role == "" {
			return nil, fmt.Errorf("host %q in %s has no role — set it to %q, %q, or %q",
				name, hostsFile, RoleSource, RoleSink, RoleBoth)
		}
		if err := ValidateRole(entry.Role); err != nil {
			return nil, fmt.Errorf("host %q in %s: %w", name, hostsFile, err)
		}
	}
	return &cfg, nil
}

// SaveHostsConfig writes hosts.yaml to the instance directory.
func SaveHostsConfig(installPath string, cfg *HostsConfig) error {
	data, err := yaml.Marshal(cfg)
	if err != nil {
		return fmt.Errorf("marshaling %s: %w", hostsFile, err)
	}
	return os.WriteFile(filepath.Join(installPath, hostsFile), data, permissions.FilePermission)
}

// LoadVars reads and parses a host's vars.yaml.
func LoadVars(installPath, hostName string) (VarsMap, error) {
	path := varsPath(installPath, hostName)
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading vars for host %q: %w", hostName, err)
	}
	var vars VarsMap
	if err := yaml.Unmarshal(data, &vars); err != nil {
		return nil, fmt.Errorf("parsing vars for host %q: %w", hostName, err)
	}
	return vars, nil
}

// SaveVars writes a host's vars.yaml.
func SaveVars(installPath, hostName string, vars VarsMap) error {
	dir := filepath.Join(installPath, "hosts", hostName)
	if err := os.MkdirAll(dir, permissions.DirectoryPermission); err != nil {
		return fmt.Errorf("creating host directory %q: %w", hostName, err)
	}
	data, err := yaml.Marshal(vars)
	if err != nil {
		return fmt.Errorf("marshaling vars for host %q: %w", hostName, err)
	}
	return os.WriteFile(varsPath(installPath, hostName), data, permissions.SecretFilePermission)
}

// LoadCustomKeys reads custom-keys.yaml if it exists. Returns an empty
// list if the file does not exist.
func LoadCustomKeys(installPath string) ([]string, error) {
	path := filepath.Join(installPath, customKeysFile)
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading %s: %w", customKeysFile, err)
	}
	var ck CustomKeys
	if err := yaml.Unmarshal(data, &ck); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", customKeysFile, err)
	}
	return ck.Keys, nil
}

// LoadEnvTemplate reads the env.template file from the instance directory.
func LoadEnvTemplate(installPath string) (string, error) {
	data, err := os.ReadFile(filepath.Join(installPath, envTemplateFile))
	if err != nil {
		return "", fmt.Errorf("reading %s: %w", envTemplateFile, err)
	}
	return string(data), nil
}

// SaveEnvTemplate writes the env.template file to the instance directory.
func SaveEnvTemplate(installPath, content string) error {
	return os.WriteFile(filepath.Join(installPath, envTemplateFile), []byte(content), permissions.FilePermission)
}

// LoadWikisTemplate reads the wikis.yaml.template file from the
// instance directory. Returns empty string and no error if the file
// does not exist (for backward compatibility with repos that predate
// wikis templating).
func LoadWikisTemplate(installPath string) (string, error) {
	data, err := os.ReadFile(filepath.Join(installPath, wikisTemplateFile))
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("reading %s: %w", wikisTemplateFile, err)
	}
	return string(data), nil
}

// SaveWikisTemplate writes the wikis.yaml.template file to the
// instance directory.
func SaveWikisTemplate(installPath, content string) error {
	return os.WriteFile(filepath.Join(installPath, wikisTemplateFile), []byte(content), permissions.FilePermission)
}

// LoadWikisYAML reads the config/wikis.yaml file.
func LoadWikisYAML(installPath string) (string, error) {
	data, err := os.ReadFile(filepath.Join(installPath, wikisFile))
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("reading %s: %w", wikisFile, err)
	}
	return string(data), nil
}

// SaveLocalHost writes the .gitops-host file to record which host name
// this instance corresponds to. The file is gitignored.
func SaveLocalHost(installPath, hostName string) error {
	path := filepath.Join(installPath, hostFile)
	return os.WriteFile(path, []byte(hostName+"\n"), permissions.FilePermission)
}

// LoadLocalHost reads the .gitops-host file. Returns an empty string if
// the file does not exist.
func LoadLocalHost(installPath string) (string, error) {
	path := filepath.Join(installPath, hostFile)
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

// FindCurrentHost identifies this server's host entry in the config
// by reading the .gitops-host file written during init.
// Returns the host entry, the host name key, and any error.
func FindCurrentHost(cfg *HostsConfig, installPath string) (*HostEntry, string, error) {
	localHost, err := LoadLocalHost(installPath)
	if err != nil {
		return nil, "", fmt.Errorf("reading %s: %w", hostFile, err)
	}
	if localHost == "" {
		return nil, "", fmt.Errorf("%s not found — run \"canasta gitops init\" first", hostFile)
	}
	entry, ok := cfg.Hosts[localHost]
	if !ok {
		return nil, "", fmt.Errorf("host %q (from %s) not found in %s", localHost, hostFile, hostsFile)
	}
	return &entry, localHost, nil
}

// ReadAdminPasswords reads all config/admin-password_* files and returns
// them as a map of wiki-id → password.
func ReadAdminPasswords(installPath string) (map[string]string, error) {
	configDir := filepath.Join(installPath, "config")
	entries, err := os.ReadDir(configDir)
	if err != nil {
		return nil, fmt.Errorf("reading config directory: %w", err)
	}
	passwords := make(map[string]string)
	for _, entry := range entries {
		if !strings.HasPrefix(entry.Name(), "admin-password_") {
			continue
		}
		wikiID := strings.TrimPrefix(entry.Name(), "admin-password_")
		data, err := os.ReadFile(filepath.Join(configDir, entry.Name()))
		if err != nil {
			return nil, fmt.Errorf("reading %s: %w", entry.Name(), err)
		}
		passwords[wikiID] = strings.TrimSpace(string(data))
	}
	return passwords, nil
}

// WriteAdminPasswords writes config/admin-password_* files from vars.
// It looks for vars keys matching "admin_password_{wikiid}" and writes
// the corresponding files.
func WriteAdminPasswords(installPath string, vars VarsMap) error {
	configDir := filepath.Join(installPath, "config")
	if err := os.MkdirAll(configDir, permissions.DirectoryPermission); err != nil {
		return fmt.Errorf("creating config directory: %w", err)
	}
	for key, value := range vars {
		if !strings.HasPrefix(key, "admin_password_") {
			continue
		}
		wikiID := strings.TrimPrefix(key, "admin_password_")
		filename := fmt.Sprintf("admin-password_%s", wikiID)
		path := filepath.Join(configDir, filename)
		if err := os.WriteFile(path, []byte(value+"\n"), permissions.SecretFilePermission); err != nil {
			return fmt.Errorf("writing %s: %w", filename, err)
		}
	}
	return nil
}

// SaveAppliedCommit records the last successfully applied commit hash.
func SaveAppliedCommit(installPath, commitHash string) error {
	path := filepath.Join(installPath, appliedFile)
	return os.WriteFile(path, []byte(commitHash+"\n"), permissions.FilePermission)
}

// LoadAppliedCommit reads the last successfully applied commit hash.
// Returns an empty string if the file does not exist.
func LoadAppliedCommit(installPath string) (string, error) {
	path := filepath.Join(installPath, appliedFile)
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

func varsPath(installPath, hostName string) string {
	return filepath.Join(installPath, "hosts", hostName, "vars.yaml")
}
