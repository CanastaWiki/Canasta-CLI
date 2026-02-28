package extensionsskins

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strings"

	"gopkg.in/yaml.v2"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
)

// configYAML represents the YAML config file managed by the CLI.
type configYAML struct {
	Extensions []string `yaml:"extensions,omitempty"`
	Skins      []string `yaml:"skins,omitempty"`
}

const configFileName = "main.yaml"
const configHeader = "# This file is managed by Canasta, which adds and removes extensions and\n# skins from it. You may edit it, but Canasta may overwrite your changes.\n"

// validNamePattern matches names consisting of alphanumeric characters,
// underscores, hyphens, and dots (e.g. "VisualEditor", "Semantic_MediaWiki").
var validNamePattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$`)

// ValidateName checks that an extension or skin name contains only safe characters.
func ValidateName(name string, constants Item) error {
	if name == "" {
		return fmt.Errorf("%s name cannot be empty", constants.CmdName)
	}
	if !validNamePattern.MatchString(name) {
		return fmt.Errorf("invalid %s name %q: must start with a letter or digit and contain only letters, digits, underscores, hyphens, and dots", constants.CmdName, name)
	}
	return nil
}

type Item struct {
	Name                     string // e.g. "Canasta extension" or "Canasta skin"
	CmdName                  string // e.g. "extension" or "skin"
	Plural                   string // e.g. "extensions" or "skins"
	RelativeInstallationPath string // e.g. "extensions" or "skins"
	PhpCommand               string // e.g. "wfLoadExtension" or "wfLoadSkin"
	ExampleNames             string // e.g. "VisualEditor,Cite,ParserFunctions" or "Timeless"
}

// configPath returns the host path to main.yaml for the given installation and wiki.
func configPath(instancePath, wiki string) string {
	if wiki != "" {
		return filepath.Join(instancePath, "config", "settings", "wikis", wiki, configFileName)
	}
	return filepath.Join(instancePath, "config", "settings", "global", configFileName)
}

// readConfig reads and parses the YAML config file. Returns an empty struct if the file does not exist.
func readConfig(path string) (configYAML, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return configYAML{}, nil
		}
		return configYAML{}, err
	}
	var cfg configYAML
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return configYAML{}, fmt.Errorf("failed to parse %s: %w", path, err)
	}
	return cfg, nil
}

// writeConfig writes the YAML config file with a header comment.
// If both slices are empty, it deletes the file instead.
func writeConfig(path string, cfg configYAML) error {
	if len(cfg.Extensions) == 0 && len(cfg.Skins) == 0 {
		err := os.Remove(path)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), permissions.DirectoryPermission); err != nil {
		return err
	}
	data, err := yaml.Marshal(&cfg)
	if err != nil {
		return err
	}
	return os.WriteFile(path, append([]byte(configHeader), data...), permissions.FilePermission)
}

// getSlice returns a pointer to the appropriate slice (extensions or skins) in the config.
func getSlice(cfg *configYAML, constants Item) *[]string {
	if constants.Plural == "skins" {
		return &cfg.Skins
	}
	return &cfg.Extensions
}

func List(instance config.Installation, orch orchestrators.Orchestrator, constants Item) error {
	fmt.Printf("Available %s:\n", constants.Name)
	output, err := orch.ExecWithError(instance.Path, orchestrators.ServiceWeb, "cd $MW_HOME/"+constants.RelativeInstallationPath+" && find -L * -maxdepth 0 -type d")
	if err != nil {
		return fmt.Errorf("failed to list %s: %s", constants.Name, output)
	}
	fmt.Print(output)
	return nil
}

func CheckInstalled(name string, instance config.Installation, orch orchestrators.Orchestrator, constants Item) (string, error) {
	if err := ValidateName(name, constants); err != nil {
		return "", err
	}
	output, err := orch.ExecWithError(instance.Path, orchestrators.ServiceWeb, "cd $MW_HOME/"+constants.RelativeInstallationPath+" && find -L * -maxdepth 0 -type d")
	if err != nil {
		return "", fmt.Errorf("failed to check installed %s: %s", constants.Name, output)
	}
	if !slices.Contains(strings.Split(output, "\n"), name) {
		return "", fmt.Errorf("%s %s doesn't exist", constants.Name, name)
	}
	return name, nil
}

func Enable(name, wiki string, instance config.Installation, constants Item) error {
	if err := ValidateName(name, constants); err != nil {
		return err
	}
	if wiki == "" {
		fmt.Println("You didn't specify a wiki. The extension or skin will affect all wikis in the corresponding Canasta instance.")
	}

	path := configPath(instance.Path, wiki)
	cfg, err := readConfig(path)
	if err != nil {
		return err
	}

	slice := getSlice(&cfg, constants)
	if slices.Contains(*slice, name) {
		fmt.Printf("%s %s is already enabled!\n", constants.Name, name)
		return nil
	}

	*slice = append(*slice, name)
	sort.Strings(*slice)

	if err := writeConfig(path, cfg); err != nil {
		return fmt.Errorf("failed to enable %s %q: %w", constants.Name, name, err)
	}
	fmt.Printf("%s %s enabled\n", constants.Name, name)
	return nil
}

func CheckEnabled(name, wiki string, instance config.Installation, constants Item) (string, error) {
	if err := ValidateName(name, constants); err != nil {
		return "", err
	}

	path := configPath(instance.Path, wiki)
	cfg, err := readConfig(path)
	if err != nil {
		return "", fmt.Errorf("failed to check if %s %q is enabled: %w", constants.Name, name, err)
	}

	slice := getSlice(&cfg, constants)
	if !slices.Contains(*slice, name) {
		return "", fmt.Errorf("%s %s is not enabled", constants.Name, name)
	}
	return name, nil
}

func Disable(name, wiki string, instance config.Installation, constants Item) error {
	if err := ValidateName(name, constants); err != nil {
		return err
	}
	if wiki == "" {
		fmt.Println("You didn't specify a wiki. The common settings will disable the extension or skin in the corresponding Canasta instance.")
	}

	path := configPath(instance.Path, wiki)
	cfg, err := readConfig(path)
	if err != nil {
		return err
	}

	slice := getSlice(&cfg, constants)
	idx := -1
	for i, item := range *slice {
		if item == name {
			idx = i
			break
		}
	}
	if idx == -1 {
		return fmt.Errorf("%s %s is not enabled", constants.Name, name)
	}

	*slice = append((*slice)[:idx], (*slice)[idx+1:]...)

	if err := writeConfig(path, cfg); err != nil {
		return fmt.Errorf("failed to disable %s %q: %w", constants.Name, name, err)
	}
	fmt.Printf("%s %s disabled\n", constants.Name, name)
	return nil
}
