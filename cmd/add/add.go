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
	var url string
	var admin string
	var adminPassword string
	var wikidbuser string

	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}

	addCmd := &cobra.Command{
		Use:   "add",
		Short: "Add a new wiki to a Canasta instance",
		RunE: func(cmd *cobra.Command, args []string) error {
			var err error

			// Validate wiki ID
			if err := farmsettings.ValidateWikiID(wikiID); err != nil {
				log.Fatal(err)
			}

			// Parse URL to extract domain and path
			urlString := url
			if !strings.HasPrefix(urlString, "http://") && !strings.HasPrefix(urlString, "https://") {
				urlString = "https://" + urlString
			}
			parsedUrl, err := urlpkg.Parse(urlString)
			if err != nil {
				log.Fatal(fmt.Errorf("failed to parse URL: %w", err))
			}
			domainName = parsedUrl.Hostname()
			wikiPath = strings.Trim(parsedUrl.Path, "/")

			// Generate admin password if not provided
			if adminPassword == "" {
				adminPassword, err = canasta.GeneratePassword("admin")
				if err != nil {
					log.Fatal(err)
				}
				fmt.Printf("Generated admin password for wiki '%s'\n", wikiID)
			}

			fmt.Printf("Adding wiki '%s' to Canasta instance '%s'...\n", wikiID, instance.Id)
			err = AddWiki(instance, wikiID, siteName, domainName, wikiPath, databasePath, admin, adminPassword, wikidbuser, workingDir)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("Done.")
			return nil
		},
	}

	addCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "ID of the new wiki")
	addCmd.Flags().StringVarP(&url, "url", "u", "", "URL of the new wiki (domain/path format, e.g., 'localhost/wiki2' or 'example.com/mywiki'; do not include protocol/scheme)")
	addCmd.Flags().StringVarP(&siteName, "site-name", "t", "", "Display name of the wiki (optional, defaults to wiki ID)")
	addCmd.Flags().StringVarP(&instance.Path, "path", "p", workingDir, "Path to the new wiki")
	addCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	addCmd.Flags().StringVarP(&instance.Orchestrator, "orchestrator", "o", "compose", "Orchestrator to use for installation")
	addCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to the existing database dump")
	addCmd.Flags().StringVarP(&admin, "admin", "a", "", "Admin name of the new wiki")
	addCmd.Flags().StringVarP(&adminPassword, "password", "s", "", "Admin password for the new wiki (if not provided, auto-generates and saves to config/admin-password_{wikiid})")
	addCmd.Flags().StringVar(&wikidbuser, "wikidbuser", "root", "The username of the wiki database user (default: \"root\")")

	// Mark required flags
	addCmd.MarkFlagRequired("wiki")
	addCmd.MarkFlagRequired("url")
	addCmd.MarkFlagRequired("id")
	addCmd.MarkFlagRequired("admin")

	return addCmd
}

// AddWiki accepts the Canasta instance info, wiki ID, site name, domain and path of the new wiki, database info, and the initial admin info, then creates a new wiki in the Canasta instance.
func AddWiki(instance config.Installation, wikiID, siteName, domain, wikipath, databasePath, admin, adminPassword, wikidbuser, workingDir string) error {
	var err error

	//Checking Installation existence
	instance, err = canasta.CheckCanastaId(instance)
	if err != nil {
		return err
	}

	//Migrate to the new version Canasta
	err = canasta.MigrateToNewVersion(instance.Path)
	if err != nil {
		return err
	}

	//Checking Running status
	err = orchestrators.CheckRunningStatus(instance.Path, instance.Id, instance.Orchestrator)
	if err != nil {
		return err
	}

	//Checking Wiki existence
	wikiIDExists, err := farmsettings.WikiIDExists(instance.Path, wikiID)
	if err != nil {
		return err
	}
	if wikiIDExists {
		return fmt.Errorf("A wiki with the ID '%s' exists", wikiID)
	}

	urlExists, err := farmsettings.WikiUrlExists(instance.Path, domain, wikipath)
	if err != nil {
		return err
	}
	if urlExists {
		return fmt.Errorf("A wiki with the same installation path '%s' in the Canasta instance '%s' exists", wikiID+": "+domain+"/"+wikipath, instance.Id)
	}

	// Import the database if databasePath is specified
	if databasePath != "" {
		err = orchestrators.ImportDatabase(wikiID, databasePath, instance)
		if err != nil {
			return err
		}
	}

	//Copy the Localsettings
	err = canasta.CopySetting(instance.Path, wikiID)
	if err != nil {
		return err
	}

	// Run MediaWiki installer - must succeed before modifying wikis.yaml
	err = mediawiki.InstallOne(instance.Path, wikiID, domain, admin, adminPassword, wikidbuser, workingDir, instance.Orchestrator)
	if err != nil {
		return err
	}

	//Add the wiki in farmsettings (only after successful installation)
	err = farmsettings.AddWiki(wikiID, instance.Path, domain, wikipath, siteName)
	if err != nil {
		return err
	}

	//Rewrite the Caddyfile (only after adding to wikis.yaml)
	err = canasta.RewriteCaddy(instance.Path)
	if err != nil {
		return err
	}
	err = restart.Restart(instance)
	if err != nil {
		return err
	}

	fmt.Println("Successfully added wiki '" + wikiID + "' in Canasta instance '" + instance.Id + "'...")

	return nil
}
