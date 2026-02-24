package config

import (
	"fmt"
	"io/ioutil"
	"net/url"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	yaml "gopkg.in/yaml.v2"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

type sideEffect struct {
	validate func(instance config.Installation, value string) error
	apply    func(instance config.Installation, value string) error
}

var sideEffects = map[string]sideEffect{
	"HTTPS_PORT": {
		validate: validatePortChange,
		apply:    applyHTTPSPortChange,
	},
	"HTTP_PORT": {
		validate: validatePortChange,
	},
	"CANASTA_ENABLE_OBSERVABILITY": {
		apply: applyObservabilityChange,
	},
}

var noRestart bool

func setCmdCreate() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "set KEY VALUE",
		Short: "Change a configuration setting",
		Long: `Set a configuration value in the .env file of a Canasta installation.

After saving the value, any side effects are applied (e.g., updating
wikis.yaml when changing HTTPS_PORT) and the instance is restarted.
Use --no-restart to skip the restart (useful for batching multiple changes).`,
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			envPath := filepath.Join(instance.Path, ".env")
			envVars, err := canasta.GetEnvVariable(envPath)
			if err != nil {
				return fmt.Errorf("failed to read .env: %w", err)
			}

			key := resolveKey(envVars, args[0])
			value := args[1]

			// Run validation if defined
			if se, ok := sideEffects[key]; ok && se.validate != nil {
				if err := se.validate(instance, value); err != nil {
					return err
				}
			}

			// Save the value
			if err := canasta.SaveEnvVariable(envPath, key, value); err != nil {
				return fmt.Errorf("failed to save %s: %w", key, err)
			}
			logging.Print(fmt.Sprintf("Saved %s=%s\n", key, value))

			// Apply side effects if defined
			if se, ok := sideEffects[key]; ok && se.apply != nil {
				if err := se.apply(instance, value); err != nil {
					return fmt.Errorf("side effect for %s failed: %w", key, err)
				}
			}

			if noRestart {
				fmt.Println("Setting saved. Restart skipped (--no-restart).")
				return nil
			}

			// Restart: UpdateConfig → Stop → Start
			fmt.Println("Applying configuration and restarting...")
			if err := orch.UpdateConfig(instance.Path); err != nil {
				return fmt.Errorf("failed to update config: %w", err)
			}
			if err := orch.Stop(instance); err != nil {
				return fmt.Errorf("failed to stop instance: %w", err)
			}
			if err := orch.Start(instance); err != nil {
				return fmt.Errorf("failed to start instance: %w", err)
			}
			fmt.Println("Done.")
			return nil
		},
	}

	cmd.Flags().BoolVar(&noRestart, "no-restart", false, "Save the setting without restarting the instance")
	return cmd
}

// validatePortChange blocks port changes on kind-managed Kubernetes instances.
func validatePortChange(inst config.Installation, value string) error {
	if inst.KindCluster != "" {
		return fmt.Errorf("port changes are not supported on kind-managed Kubernetes instances")
	}
	return nil
}

// applyHTTPSPortChange updates wikis.yaml URLs and MW_SITE_SERVER/MW_SITE_FQDN
// in .env to reflect the new HTTPS port.
func applyHTTPSPortChange(inst config.Installation, newPort string) error {
	wikisPath := filepath.Join(inst.Path, "config", "wikis.yaml")
	envPath := filepath.Join(inst.Path, ".env")

	// Read wikis.yaml
	data, err := ioutil.ReadFile(wikisPath)
	if err != nil {
		return fmt.Errorf("failed to read wikis.yaml: %w", err)
	}
	var wikis farmsettings.Wikis
	if err := yaml.Unmarshal(data, &wikis); err != nil {
		return fmt.Errorf("failed to parse wikis.yaml: %w", err)
	}

	// Update each wiki URL
	for i, wiki := range wikis.Wikis {
		wikis.Wikis[i].URL = updateURLPort(wiki.URL, newPort)
	}

	// Write wikis.yaml back
	out, err := yaml.Marshal(&wikis)
	if err != nil {
		return fmt.Errorf("failed to marshal wikis.yaml: %w", err)
	}
	if err := ioutil.WriteFile(wikisPath, out, 0644); err != nil {
		return fmt.Errorf("failed to write wikis.yaml: %w", err)
	}
	logging.Print("Updated wikis.yaml with new port\n")

	// Update MW_SITE_SERVER and MW_SITE_FQDN in .env
	envVars, err := canasta.GetEnvVariable(envPath)
	if err != nil {
		return fmt.Errorf("failed to read .env: %w", err)
	}

	if siteServer := envVars["MW_SITE_SERVER"]; siteServer != "" {
		updated := updateSiteServerPort(siteServer, newPort)
		if err := canasta.SaveEnvVariable(envPath, "MW_SITE_SERVER", updated); err != nil {
			return fmt.Errorf("failed to update MW_SITE_SERVER: %w", err)
		}
		logging.Print(fmt.Sprintf("Updated MW_SITE_SERVER to %s\n", updated))
	}

	if siteFQDN := envVars["MW_SITE_FQDN"]; siteFQDN != "" {
		updated := updateURLPort(siteFQDN, newPort)
		if err := canasta.SaveEnvVariable(envPath, "MW_SITE_FQDN", updated); err != nil {
			return fmt.Errorf("failed to update MW_SITE_FQDN: %w", err)
		}
		logging.Print(fmt.Sprintf("Updated MW_SITE_FQDN to %s\n", updated))
	}

	return nil
}

// updateURLPort takes a domain or domain:port string (no scheme) and returns
// it with the new port. If newPort is "443", the port suffix is omitted.
func updateURLPort(domainWithPath, newPort string) string {
	// Split off any path component
	domain := domainWithPath
	path := ""
	if idx := strings.Index(domainWithPath, "/"); idx != -1 {
		domain = domainWithPath[:idx]
		path = domainWithPath[idx:]
	}

	// Strip existing port
	if colonIdx := strings.LastIndex(domain, ":"); colonIdx != -1 {
		domain = domain[:colonIdx]
	}

	// Add new port unless it's the standard HTTPS port
	if newPort != "443" {
		domain = domain + ":" + newPort
	}

	return domain + path
}

// updateSiteServerPort updates the port in a full URL like https://example.com:8443.
func updateSiteServerPort(siteServer, newPort string) string {
	u, err := url.Parse(siteServer)
	if err != nil {
		// Fallback: treat as domain
		return updateURLPort(siteServer, newPort)
	}
	host := u.Hostname()
	if newPort != "443" {
		u.Host = host + ":" + newPort
	} else {
		u.Host = host
	}
	return u.String()
}

// applyObservabilityChange generates observability credentials when enabling.
func applyObservabilityChange(inst config.Installation, value string) error {
	if !strings.EqualFold(value, "true") {
		return nil
	}
	_, err := canasta.EnsureObservabilityCredentials(inst.Path)
	return err
}
