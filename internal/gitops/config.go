package gitops

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v2"
)

const (
	hostsFile       = "hosts.yaml"
	customKeysFile  = "custom-keys.yaml"
	envTemplateFile = "env.template"
	appliedFile     = ".gitops-applied"
)

// LoadHostsConfig reads and parses hosts.yaml from the installation directory.
func LoadHostsConfig(installPath string) (*HostsConfig, error) {
	data, err := os.ReadFile(filepath.Join(installPath, hostsFile))
	if err != nil {
		return nil, fmt.Errorf("reading %s: %w", hostsFile, err)
	}
	var cfg HostsConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", hostsFile, err)
	}
	// Apply default role for single-host configs.
	if len(cfg.Hosts) == 1 {
		for name, entry := range cfg.Hosts {
			if entry.Role == "" {
				entry.Role = RoleBoth
				cfg.Hosts[name] = entry
			}
		}
	}
	return &cfg, nil
}

// SaveHostsConfig writes hosts.yaml to the installation directory.
func SaveHostsConfig(installPath string, cfg *HostsConfig) error {
	data, err := yaml.Marshal(cfg)
	if err != nil {
		return fmt.Errorf("marshaling %s: %w", hostsFile, err)
	}
	return os.WriteFile(filepath.Join(installPath, hostsFile), data, 0644)
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
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("creating host directory %q: %w", hostName, err)
	}
	data, err := yaml.Marshal(vars)
	if err != nil {
		return fmt.Errorf("marshaling vars for host %q: %w", hostName, err)
	}
	return os.WriteFile(varsPath(installPath, hostName), data, 0644)
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

// LoadEnvTemplate reads the env.template file from the installation directory.
func LoadEnvTemplate(installPath string) (string, error) {
	data, err := os.ReadFile(filepath.Join(installPath, envTemplateFile))
	if err != nil {
		return "", fmt.Errorf("reading %s: %w", envTemplateFile, err)
	}
	return string(data), nil
}

// SaveEnvTemplate writes the env.template file to the installation directory.
func SaveEnvTemplate(installPath, content string) error {
	return os.WriteFile(filepath.Join(installPath, envTemplateFile), []byte(content), 0644)
}

// FindCurrentHost matches the system hostname against hosts in the config.
// Returns the host entry, the host name key, and any error.
func FindCurrentHost(cfg *HostsConfig) (*HostEntry, string, error) {
	hostname, err := os.Hostname()
	if err != nil {
		return nil, "", fmt.Errorf("getting system hostname: %w", err)
	}
	for name, entry := range cfg.Hosts {
		if entry.Hostname == hostname {
			return &entry, name, nil
		}
	}
	return nil, "", fmt.Errorf("no host entry in %s matches hostname %q", hostsFile, hostname)
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
	if err := os.MkdirAll(configDir, 0755); err != nil {
		return fmt.Errorf("creating config directory: %w", err)
	}
	for key, value := range vars {
		if !strings.HasPrefix(key, "admin_password_") {
			continue
		}
		wikiID := strings.TrimPrefix(key, "admin_password_")
		filename := fmt.Sprintf("admin-password_%s", wikiID)
		path := filepath.Join(configDir, filename)
		if err := os.WriteFile(path, []byte(value+"\n"), 0600); err != nil {
			return fmt.Errorf("writing %s: %w", filename, err)
		}
	}
	return nil
}

// SaveAppliedCommit records the last successfully applied commit hash.
func SaveAppliedCommit(installPath, commitHash string) error {
	path := filepath.Join(installPath, appliedFile)
	return os.WriteFile(path, []byte(commitHash+"\n"), 0644)
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
