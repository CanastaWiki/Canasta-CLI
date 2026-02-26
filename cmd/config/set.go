package config

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	yaml "gopkg.in/yaml.v2"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

type sideEffect struct {
	validate func(instance config.Installation, value string) error
	apply    func(instance config.Installation, value string) error
	unapply  func(instance config.Installation) error
}

var sideEffects = map[string]sideEffect{
	"HTTPS_PORT": {
		validate: validatePort,
		apply:    applyHTTPSPortChange,
		unapply: func(inst config.Installation) error {
			return applyHTTPSPortChange(inst, "443")
		},
	},
	"HTTP_PORT": {
		validate: validatePort,
	},
	"CANASTA_ENABLE_OBSERVABILITY": {
		apply: applyObservabilityChange,
	},
}

// portKeys are env vars that require a kind cluster recreation on change.
var portKeys = map[string]bool{
	"HTTP_PORT":  true,
	"HTTPS_PORT": true,
}

// knownKeys lists the configuration keys that are safe to set via
// "canasta config set". Keys not in this list (and not matching a
// resticPrefixes entry) are rejected unless --force is used.
var knownKeys = map[string]bool{
	// Network
	"HTTP_PORT":  true,
	"HTTPS_PORT": true,
	// PHP
	"PHP_UPLOAD_MAX_FILESIZE": true,
	"PHP_POST_MAX_SIZE":       true,
	"PHP_MAX_INPUT_VARS":      true,
	// Sitemaps
	"MW_SITEMAP_PAUSE_DAYS": true,
	// Features
	"CANASTA_ENABLE_ELASTICSEARCH":  true,
	"CANASTA_ENABLE_OBSERVABILITY":  true,
	"CANASTA_ENABLE_WIKI_DIRECTORY": true,
	// Caddy / TLS
	"CADDY_AUTO_HTTPS": true,
	// Docker Image
	"CANASTA_IMAGE": true,
	// Backup (Restic)
	"RESTIC_REPOSITORY": true,
	"RESTIC_PASSWORD":   true,
}

// resticPrefixes lists key prefixes for Restic backend credentials
// (e.g., AWS_ACCESS_KEY_ID, AZURE_ACCOUNT_NAME). Any key matching one
// of these prefixes is treated as known.
var resticPrefixes = []string{"AWS_", "AZURE_", "B2_", "GOOGLE_", "OS_", "ST_", "RCLONE_"}

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

var noRestart bool

// setting is a parsed KEY=VALUE pair.
type setting struct {
	key   string
	value string
}

func setCmdCreate() *cobra.Command {
	var force bool
	cmd := &cobra.Command{
		Use:   "set KEY=VALUE [KEY=VALUE ...]",
		Short: "Change a configuration setting",
		Long: `Set one or more configuration values in the .env file of a Canasta installation.

Each argument must be in KEY=VALUE format. Multiple settings can be changed
in a single invocation and only one restart is performed.

After saving the values, any side effects are applied (e.g., updating
wikis.yaml when changing HTTPS_PORT) and the instance is restarted.
Use --no-restart to skip the restart (useful for batching multiple changes).`,
		Example: `  canasta config set HTTPS_PORT=8443 -i myinstance
  canasta config set HTTP_PORT=8080 HTTPS_PORT=8443 -i myinstance
  canasta config set CANASTA_ENABLE_OBSERVABILITY=true -i myinstance`,
		Args: cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			envPath := filepath.Join(instance.Path, ".env")
			envVars, err := canasta.GetEnvVariable(envPath)
			if err != nil {
				return fmt.Errorf("failed to read .env: %w", err)
			}

			// Parse all KEY=VALUE args
			settings := make([]setting, 0, len(args))
			for _, arg := range args {
				eqIdx := strings.Index(arg, "=")
				if eqIdx < 1 {
					return fmt.Errorf("invalid argument %q: expected KEY=VALUE format", arg)
				}
				key := resolveKey(envVars, arg[:eqIdx])
				value := arg[eqIdx+1:]
				settings = append(settings, setting{key, value})
			}

			// Reject unrecognized keys unless --force is used
			if !force {
				for _, s := range settings {
					if !isKnownKey(s.key) {
						return fmt.Errorf("unrecognized setting %q\nUse 'canasta config set --force %s=%s' to set it anyway\nRun 'canasta config --help' to see available settings", s.key, s.key, s.value)
					}
				}
			}

			// Validate all before saving any
			for _, s := range settings {
				if se, ok := sideEffects[s.key]; ok && se.validate != nil {
					if err := se.validate(instance, s.value); err != nil {
						return err
					}
				}
			}

			// Save all values
			for _, s := range settings {
				if err := canasta.SaveEnvVariable(envPath, s.key, s.value); err != nil {
					return fmt.Errorf("failed to save %s: %w", s.key, err)
				}
				logging.Print(fmt.Sprintf("Saved %s=%s\n", s.key, s.value))
			}

			// Apply side effects
			for _, s := range settings {
				if se, ok := sideEffects[s.key]; ok && se.apply != nil {
					if err := se.apply(instance, s.value); err != nil {
						return fmt.Errorf("side effect for %s failed: %w", s.key, err)
					}
				}
			}

			if noRestart {
				fmt.Println("Settings saved. Restart skipped (--no-restart).")
				return nil
			}

			// Restart: UpdateConfig → Stop → (recreate kind cluster if needed) → Start
			fmt.Println("Applying configuration and restarting...")
			if err := orch.UpdateConfig(instance.Path); err != nil {
				return fmt.Errorf("failed to update config: %w", err)
			}
			if err := orch.Stop(instance); err != nil {
				return fmt.Errorf("failed to stop instance: %w", err)
			}
			// Recreate kind cluster at most once if any port key changed
			if instance.KindCluster != "" {
				for _, s := range settings {
					if portKeys[s.key] {
						if err := recreateKindCluster(instance); err != nil {
							return err
						}
						break
					}
				}
			}
			if err := orch.Start(instance); err != nil {
				return fmt.Errorf("failed to start instance: %w", err)
			}
			fmt.Println("Done.")
			return nil
		},
	}

	cmd.Flags().BoolVar(&noRestart, "no-restart", false, "Save the setting without restarting the instance")
	cmd.Flags().BoolVarP(&force, "force", "f", false, "Allow setting unrecognized keys")
	return cmd
}

// validatePort checks that the value is a valid port number.
func validatePort(inst config.Installation, value string) error {
	port, err := strconv.Atoi(value)
	if err != nil || port < 1 || port > 65535 {
		return fmt.Errorf("invalid port number: %s", value)
	}
	return nil
}

// applyHTTPSPortChange updates wikis.yaml URLs and MW_SITE_SERVER/MW_SITE_FQDN
// in .env to reflect the new HTTPS port.
func applyHTTPSPortChange(inst config.Installation, newPort string) error {
	wikisPath := filepath.Join(inst.Path, "config", "wikis.yaml")
	envPath := filepath.Join(inst.Path, ".env")

	// Read wikis.yaml
	data, err := os.ReadFile(wikisPath)
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
	if err := os.WriteFile(wikisPath, out, 0644); err != nil {
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

// recreateKindCluster deletes and recreates the kind cluster with the current
// port settings from .env so the new host-port mappings take effect.
func recreateKindCluster(inst config.Installation) error {
	httpPort, httpsPort := orchestrators.GetPortsFromEnv(inst.Path)
	logging.Print(fmt.Sprintf("Recreating kind cluster %s with ports %d/%d...\n", inst.KindCluster, httpPort, httpsPort))

	if err := orchestrators.DeleteKindCluster(inst.KindCluster); err != nil {
		return fmt.Errorf("failed to delete kind cluster: %w", err)
	}
	if err := orchestrators.CreateKindCluster(inst.KindCluster, httpPort, httpsPort); err != nil {
		return fmt.Errorf("failed to recreate kind cluster: %w", err)
	}
	return nil
}

// applyObservabilityChange generates observability credentials when enabling.
func applyObservabilityChange(inst config.Installation, value string) error {
	if !strings.EqualFold(value, "true") {
		return nil
	}
	_, err := canasta.EnsureObservabilityCredentials(inst.Path)
	return err
}
