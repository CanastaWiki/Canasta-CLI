package add

import (
	"fmt"
	"log"
	urlpkg "net/url"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/cmd/restart"
	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// URL Parameter Usage in canasta add
//
// The --url parameter specifies where the new wiki will be accessible. It uses domain/path format
// (e.g., "localhost/wiki2" or "example.com/docs") without protocol/scheme.
//
// How the URL is processed:
//
// 1. Parsing: The URL is split into domain and path components
//    Example: "localhost/wiki2" → domain="localhost", path="wiki2"
//    Example: "example.com" → domain="example.com", path=""
//
// 2. Storage in wikis.yaml: The complete URL (domain/path) is stored
//    wikis:
//    - id: wiki1
//      url: localhost           # Default wiki at root
//    - id: wiki2
//      url: localhost/wiki2     # Second wiki with path
//    - id: wiki3
//      url: example.com         # Different domain
//
// 3. Caddyfile Generation: Only unique domains are added to Caddyfile (paths are not included)
//    Result: "localhost:{$HTTPS_PORT}, example.com:{$HTTPS_PORT}"
//
//    - Multiple wikis on the same domain (e.g., localhost and localhost/wiki2) result in
//      a single Caddyfile entry
//    - Subdomains are treated as separate domains (e.g., example.com and docs.example.com
//      both appear in Caddyfile)
//    - Caddy handles SSL/HTTPS for all listed domains and routes all traffic to Varnish
//
// 4. Wiki Routing: MediaWiki uses the full URL (domain + path) from wikis.yaml to determine
//    which wiki to serve based on the incoming request's Host header and path
//
// Network Flow:
// Client → Caddy (SSL/domain routing) → Varnish (caching) → Apache → MediaWiki (wiki routing based on URL)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var wikiID string
	var domainName string
	var wikiPath string
	var siteName string
	var databasePath string
	var wikiSettingsPath string
	var url string
	var admin string
	var adminPassword string
	var wikidbuser string

	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	addCmd := &cobra.Command{
		Use:   "add",
		Short: "Add a new wiki to a Canasta instance",
		Long: `Add a new wiki to an existing Canasta installation, creating a wiki farm.
The new wiki is registered in wikis.yaml, the Caddyfile is regenerated,
and the MediaWiki installer runs for the new wiki. You can also import
an existing database dump instead of running the installer.`,
		Example: `  # Add a wiki accessible at localhost/docs
  canasta add -i myinstance -w docs -u localhost/docs

  # Add a wiki on a different domain
  canasta add -i myinstance -w blog -u blog.example.com

  # Add a wiki with an existing database dump
  canasta add -i myinstance -w docs -u localhost/docs -d /path/to/dump.sql`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) > 0 {
				return fmt.Errorf("unknown argument %q; use flags to specify options (e.g. canasta add --wiki <wiki> --url <url>)", args[0])
			}
			var err error

			// Validate wiki ID
			if err := farmsettings.ValidateWikiID(wikiID); err != nil {
				return err
			}

			// Parse URL to extract domain and path
			urlString := url
			if !strings.HasPrefix(urlString, "http://") && !strings.HasPrefix(urlString, "https://") {
				urlString = "https://" + urlString
			}
			parsedUrl, err := urlpkg.Parse(urlString)
			if err != nil {
				return fmt.Errorf("failed to parse URL: %w", err)
			}
			domainName = parsedUrl.Host
			wikiPath = strings.Trim(parsedUrl.Path, "/")

			// Validate wiki URL path
			if err := farmsettings.ValidateWikiPath(wikiPath); err != nil {
				return err
			}

			// Validate database path if provided
			if databasePath != "" {
				if err := canasta.ValidateDatabasePath(databasePath); err != nil {
					return err
				}
			}

			// Generate admin password only if not importing and admin is provided
			if databasePath == "" && adminPassword == "" {
				adminPassword, err = canasta.GeneratePassword("admin")
				if err != nil {
					return err
				}
				fmt.Printf("Generated admin password for wiki '%s'\n", wikiID)
			}

			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}

			fmt.Printf("Adding wiki '%s' to Canasta instance '%s'...\n", wikiID, instance.Id)
			err = AddWiki(AddWikiOptions{
				Instance:         instance,
				WikiID:           wikiID,
				SiteName:         siteName,
				Domain:           domainName,
				WikiPath:         wikiPath,
				DatabasePath:     databasePath,
				WikiSettingsPath: wikiSettingsPath,
				Admin:            admin,
				AdminPassword:    adminPassword,
				WikiDBUser:       wikidbuser,
				WorkingDir:       workingDir,
			})
			if err != nil {
				return err
			}
			fmt.Println("Done.")
			return nil
		},
	}

	addCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "ID of the new wiki")
	addCmd.Flags().StringVarP(&url, "url", "u", "", "URL of the new wiki (domain/path format, e.g., 'localhost/wiki2' or 'example.com/mywiki'; do not include protocol/scheme)")
	addCmd.Flags().StringVarP(&siteName, "site-name", "t", "", "Display name of the wiki (optional, defaults to wiki ID)")
	addCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	addCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to existing database dump (.sql or .sql.gz) to import instead of running install.php")
	addCmd.Flags().StringVarP(&wikiSettingsPath, "wiki-settings", "l", "", "Path to per-wiki settings file to copy to config/settings/wikis/<wiki_id>/ (filename preserved)")
	addCmd.Flags().StringVarP(&admin, "admin", "a", "WikiSysop", "Admin name of the new wiki (default: \"WikiSysop\")")
	addCmd.Flags().StringVarP(&adminPassword, "password", "s", "", "Admin password for the new wiki (if not provided, auto-generates and saves to config/admin-password_{wikiid})")
	addCmd.Flags().StringVar(&wikidbuser, "wikidbuser", "root", "The username of the wiki database user (default: \"root\")")

	// Mark required flags
	_ = addCmd.MarkFlagRequired("wiki")
	_ = addCmd.MarkFlagRequired("url")

	return addCmd
}

// AddWikiOptions contains all the parameters needed to add a wiki to a Canasta instance.
type AddWikiOptions struct {
	Instance         config.Installation
	WikiID           string
	SiteName         string
	Domain           string
	WikiPath         string
	DatabasePath     string
	WikiSettingsPath string
	Admin            string
	AdminPassword    string
	WikiDBUser       string
	WorkingDir       string
}

// AddWiki accepts the Canasta instance info, wiki ID, site name, domain and path of the new wiki, database info, and the initial admin info, then creates a new wiki in the Canasta instance.
func AddWiki(opts AddWikiOptions) error {
	orch, err := orchestrators.New(opts.Instance.Orchestrator)
	if err != nil {
		return err
	}

	//Migrate to the new version Canasta
	err = canasta.MigrateToNewVersion(opts.Instance.Path)
	if err != nil {
		return err
	}

	//Checking Running status
	err = orch.CheckRunningStatus(opts.Instance)
	if err != nil {
		return err
	}

	//Checking Wiki existence
	wikiIDExists, err := farmsettings.WikiIDExists(opts.Instance.Path, opts.WikiID)
	if err != nil {
		return err
	}
	if wikiIDExists {
		return fmt.Errorf("A wiki with the ID '%s' exists", opts.WikiID)
	}

	urlExists, err := farmsettings.WikiUrlExists(opts.Instance.Path, opts.Domain, opts.WikiPath)
	if err != nil {
		return err
	}
	if urlExists {
		return fmt.Errorf("A wiki with the same installation path '%s' in the Canasta instance '%s' exists", opts.WikiID+": "+opts.Domain+"/"+opts.WikiPath, opts.Instance.Id)
	}

	// Import the database if databasePath is specified
	if opts.DatabasePath != "" {
		envVariables, envErr := canasta.GetEnvVariable(opts.Instance.Path + "/.env")
		if envErr != nil {
			return envErr
		}
		dbPassword := envVariables["MYSQL_PASSWORD"]
		err = orchestrators.ImportDatabase(orch, opts.WikiID, opts.DatabasePath, dbPassword, opts.Instance)
		if err != nil {
			return err
		}
	}

	// Copy Settings.php - use custom file if provided, otherwise use template
	if opts.WikiSettingsPath != "" {
		err = canasta.CopyWikiSettingFile(opts.Instance.Path, opts.WikiID, opts.WikiSettingsPath, opts.WorkingDir)
		if err != nil {
			return err
		}
	} else {
		err = canasta.CopySetting(opts.Instance.Path, opts.WikiID)
		if err != nil {
			return err
		}
	}

	// Run MediaWiki installer only if not importing a database
	if opts.DatabasePath == "" {
		err = mediawiki.InstallOne(opts.Instance.Path, opts.WikiID, opts.Domain, opts.Admin, opts.AdminPassword, opts.WikiDBUser, opts.WorkingDir, orch)
		if err != nil {
			return err
		}
	}

	//Add the wiki in farmsettings (only after successful installation)
	err = farmsettings.AddWiki(opts.WikiID, opts.Instance.Path, opts.Domain, opts.WikiPath, opts.SiteName)
	if err != nil {
		return err
	}

	//Rewrite the Caddyfile (only after adding to wikis.yaml)
	err = orch.UpdateConfig(opts.Instance.Path)
	if err != nil {
		return err
	}

	err = restart.Restart(opts.Instance)
	if err != nil {
		return err
	}

	fmt.Println("Successfully added wiki '" + opts.WikiID + "' in Canasta instance '" + opts.Instance.Id + "'...")

	return nil
}
