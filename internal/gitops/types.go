package gitops

import "fmt"

// HostsConfig represents the top-level hosts.yaml structure.
type HostsConfig struct {
	CanastaID    string               `yaml:"canasta_id"`
	PullRequests bool                 `yaml:"pull_requests"`
	Hosts        map[string]HostEntry `yaml:"hosts"`
}

// HostEntry represents a single host in hosts.yaml.
type HostEntry struct {
	Hostname string `yaml:"hostname"`
	Role     string `yaml:"role,omitempty"`
}

// CustomKeys represents the custom-keys.yaml structure.
type CustomKeys struct {
	Keys []string `yaml:"keys"`
}

// VarsMap holds key-value pairs from a host's vars.yaml.
type VarsMap map[string]string

// PullResult contains the outcome of a pull operation.
type PullResult struct {
	ChangedFiles      []string
	NeedsRestart      bool
	NeedsMaintenance  bool
	SubmodulesUpdated []string
	PreviousCommit    string
	CurrentCommit     string
	NoChanges         bool
}

// PushResult contains the outcome of a push operation.
type PushResult struct {
	CommitHash string
	Branch     string
	PRURL      string
	NoChanges  bool
}

// InitResult contains the outcome of an init operation.
type InitResult struct {
	KeyFile     string
	HostName    string
	Role        string
	TemplateVar int // number of placeholders created
}

// RoleSource indicates a host that can push to the repo.
const RoleSource = "source"

// RoleSink indicates a host that can only pull from the repo.
const RoleSink = "sink"

// RoleBoth indicates a host that can both push and pull.
const RoleBoth = "both"

// ValidateRole returns an error if the role is not one of the known values.
func ValidateRole(role string) error {
	switch role {
	case RoleSource, RoleSink, RoleBoth:
		return nil
	default:
		return fmt.Errorf("invalid role %q: must be %q, %q, or %q", role, RoleSource, RoleSink, RoleBoth)
	}
}

// builtinSecretKeys are .env keys whose values are secrets and must
// differ per host. These become {{placeholders}} in env.template.
var builtinSecretKeys = []string{
	"MYSQL_PASSWORD",
	"WIKI_DB_PASSWORD",
	"MW_SECRET_KEY",
}

// builtinHostKeys are .env keys whose values are host-specific but not
// necessarily secret. These also become {{placeholders}} in env.template.
var builtinHostKeys = []string{
	"MW_SITE_SERVER",
	"MW_SITE_FQDN",
	"HTTPS_PORT",
	"HTTP_PORT",
}
