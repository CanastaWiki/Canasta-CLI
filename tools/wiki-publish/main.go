// wiki-publish generates wikitext reference pages from Canasta CLI commands
// and publishes them to a MediaWiki wiki.
//
// Usage:
//
//	go run tools/wiki-publish/main.go \
//	  -api https://example.com/w/api.php \
//	  -user User@BotName \
//	  -pass botpassword
//
// Use -dry-run to generate wikitext files locally without uploading.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"

	cmd "github.com/CanastaWiki/Canasta-CLI/cmd/root"
)

const (
	referencePrefix = "CLI:"
	editDelay       = 2 * time.Second
)

func main() {
	apiURL := flag.String("api", "", "MediaWiki API URL (e.g., https://example.com/w/api.php)")
	user := flag.String("user", "", "Bot username (e.g., User@BotName)")
	pass := flag.String("pass", "", "Bot password")
	outDir := flag.String("out", "", "Write wikitext files to this directory (optional)")
	dryRun := flag.Bool("dry-run", false, "Generate wikitext but do not upload")
	flag.Parse()

	if !*dryRun && (*apiURL == "" || *user == "" || *pass == "") {
		fmt.Fprintln(os.Stderr, "Error: -api, -user, and -pass are required (or use -dry-run)")
		flag.Usage()
		os.Exit(1)
	}

	rootCmd := cmd.GetRootCmd()
	rootCmd.DisableAutoGenTag = true

	var pages []wikiPage
	collectPages(rootCmd, &pages)

	// CLI Reference menu — commands grouped into logical categories.
	// Labels are prefixed with "canasta" to avoid colliding with
	// MediaWiki system messages (e.g. "create" → "Create account").
	type cmdGroup struct {
		heading string
		names   []string
	}
	cmdGroups := []cmdGroup{
		{"Installation", []string{"create", "delete", "list", "upgrade", "version", "config"}},
		{"Wiki Management", []string{"add", "remove", "import", "export"}},
		{"Container Lifecycle", []string{"start", "stop", "restart"}},
		{"Extensions & Skins", []string{"extension", "skin"}},
		{"Maintenance", []string{"maintenance", "sitemap"}},
		{"Data Protection", []string{"backup", "gitops"}},
		{"Development", []string{"devmode"}},
	}

	// Index subcommands by name for lookup
	cmdByName := make(map[string]*cobra.Command)
	for _, child := range rootCmd.Commands() {
		if child.IsAvailableCommand() && !child.IsAdditionalHelpTopicCommand() {
			cmdByName[child.Name()] = child
		}
	}

	var cliRef strings.Builder
	cliRef.WriteString("* # | CLI Reference\n")
	for _, g := range cmdGroups {
		cliRef.WriteString(fmt.Sprintf("** # | %s\n", g.heading))
		for _, name := range g.names {
			child, ok := cmdByName[name]
			if !ok {
				continue
			}
			link := referencePrefix + strings.ReplaceAll(child.CommandPath(), " ", "_")
			cliRef.WriteString(fmt.Sprintf("*** %s | canasta %s\n", link, child.Name()))
			genMenuCommands(&cliRef, child, 4)
		}
	}
	pages = append(pages, wikiPage{
		Title:   "MediaWiki:Menu-cli-reference",
		Content: cliRef.String(),
	})

	// Optionally write to disk
	if *outDir != "" {
		if err := os.MkdirAll(*outDir, 0755); err != nil {
			log.Fatalf("Failed to create output dir: %v", err)
		}
		for _, p := range pages {
			filename := strings.ReplaceAll(p.Title, ":", "_") + ".wiki"
			filename = strings.ReplaceAll(filename, "/", "_")
			path := filepath.Join(*outDir, filename)
			if err := os.WriteFile(path, []byte(p.Content), 0644); err != nil {
				log.Fatalf("Failed to write %s: %v", path, err)
			}
			log.Printf("Wrote %s", path)
		}
	}

	if *dryRun {
		log.Printf("Dry run: %d pages generated", len(pages))
		if *outDir == "" {
			for _, p := range pages {
				fmt.Printf("=== %s ===\n%s\n\n", p.Title, p.Content)
			}
		}
		return
	}

	// Upload to wiki
	client, err := newMediaWikiClient(*apiURL, *user, *pass)
	if err != nil {
		log.Fatalf("Failed to login: %v", err)
	}

	for i, p := range pages {
		if i > 0 {
			time.Sleep(editDelay)
		}
		if err := client.editPage(p.Title, p.Content, "Update CLI reference"); err != nil {
			log.Printf("ERROR uploading %s: %v", p.Title, err)
		} else {
			log.Printf("Published %s", p.Title)
		}
	}
	log.Printf("Done: %d pages published", len(pages))
}

type wikiPage struct {
	Title   string
	Content string
}

func hasAvailableSubcommands(c *cobra.Command) bool {
	for _, sub := range c.Commands() {
		if sub.IsAvailableCommand() && !sub.IsAdditionalHelpTopicCommand() {
			return true
		}
	}
	return false
}

func collectPages(c *cobra.Command, pages *[]wikiPage) {
	title := referencePrefix + strings.ReplaceAll(c.CommandPath(), " ", "_")
	content := genWikitext(c)
	*pages = append(*pages, wikiPage{Title: title, Content: content})

	for _, sub := range c.Commands() {
		if !sub.IsAvailableCommand() || sub.IsAdditionalHelpTopicCommand() {
			continue
		}
		collectPages(sub, pages)
	}
}

func genWikitext(c *cobra.Command) string {
	c.InitDefaultHelpCmd()
	c.InitDefaultHelpFlag()

	var b strings.Builder

	// Breadcrumb: canasta > backup > create
	if c.HasParent() {
		var crumbs []string
		for p := c.Parent(); p != nil; p = p.Parent() {
			link := referencePrefix + strings.ReplaceAll(p.CommandPath(), " ", "_")
			crumbs = append([]string{fmt.Sprintf("[[%s|%s]]", link, p.Name())}, crumbs...)
		}
		b.WriteString(strings.Join(crumbs, " > ") + " > " + c.Name() + "\n\n")
	}

	name := c.CommandPath()
	b.WriteString("== " + name + " ==\n\n")
	b.WriteString(c.Short + "\n\n")

	if len(c.Long) > 0 {
		b.WriteString("=== Synopsis ===\n\n")
		b.WriteString(c.Long + "\n\n")
	}

	if c.Runnable() {
		b.WriteString("<pre>\n")
		b.WriteString(c.UseLine() + "\n")
		b.WriteString("</pre>\n\n")
	}

	// Subcommands section for non-runnable commands
	if hasAvailableSubcommands(c) {
		b.WriteString("=== Subcommands ===\n\n")
		if !c.Runnable() {
			b.WriteString("This command requires a subcommand:\n\n")
		}
		children := c.Commands()
		sort.Sort(byName(children))
		for _, child := range children {
			if !child.IsAvailableCommand() || child.IsAdditionalHelpTopicCommand() {
				continue
			}
			cname := name + " " + child.Name()
			link := referencePrefix + strings.ReplaceAll(cname, " ", "_")
			b.WriteString(fmt.Sprintf("* [[%s|%s]] — %s\n", link, child.Name(), child.Short))
		}
		b.WriteString("\n")
	}

	if len(c.Example) > 0 {
		b.WriteString("=== Examples ===\n\n")
		b.WriteString("<pre>\n")
		b.WriteString(c.Example + "\n")
		b.WriteString("</pre>\n\n")
	}

	// Combined flags
	if hasAnyFlags(c) {
		b.WriteString("=== Flags ===\n\n")
		b.WriteString(allFlagsToWikiTable(c))
		b.WriteString("\n")
	}

	return b.String()
}

func hasAnyFlags(c *cobra.Command) bool {
	found := false
	c.NonInheritedFlags().VisitAll(func(f *pflag.Flag) {
		if !f.Hidden {
			found = true
		}
	})
	if found {
		return true
	}
	c.InheritedFlags().VisitAll(func(f *pflag.Flag) {
		if !f.Hidden {
			found = true
		}
	})
	return found
}

// isFlagRequired checks whether a flag is marked required via Cobra's
// MarkFlagRequired annotation.
func isFlagRequired(f *pflag.Flag) bool {
	ann, ok := f.Annotations[cobra.BashCompOneRequiredFlag]
	return ok && len(ann) > 0 && ann[0] == "true"
}

// reParenDefault matches "(default: ...)" or "(defaults to ...)" or "(optional, defaults to ...)"
// at the end of a flag description.
var reParenDefault = regexp.MustCompile(`\s*\((?:optional, )?default(?:s to|:)\s*"?([^")]+)"?\)`)

func extractDefault(description string) string {
	m := reParenDefault.FindStringSubmatch(description)
	if m == nil {
		return ""
	}
	return m[1]
}

func allFlagsToWikiTable(c *cobra.Command) string {
	var b strings.Builder
	b.WriteString("{| class=\"wikitable\"\n")
	b.WriteString("! Flag !! Shorthand !! Description !! Default !! style=\"text-align:center\" | Required\n")

	seen := make(map[string]bool)
	writeFlag := func(f *pflag.Flag) {
		if f.Hidden || seen[f.Name] {
			return
		}
		seen[f.Name] = true

		shorthand := ""
		if f.Shorthand != "" {
			shorthand = "<code>-" + f.Shorthand + "</code>"
		}

		name := "<code>--" + f.Name + "</code>"
		description := strings.ReplaceAll(f.Usage, "\n", " ")

		required := ""
		if isFlagRequired(f) {
			required = "✓"
		}

		// Extract parenthetical defaults from description into the Default column
		defaultVal := ""
		if extracted := extractDefault(description); extracted != "" {
			description = strings.TrimSpace(reParenDefault.ReplaceAllString(description, ""))
			defaultVal = extracted
		} else if f.DefValue != "" && f.DefValue != "false" && f.DefValue != "0" && f.DefValue != "[]" {
			defaultVal = "<code>" + f.DefValue + "</code>"
		}

		b.WriteString("|-\n")
		b.WriteString(fmt.Sprintf("| %s || %s || %s || %s || style=\"text-align:center\" | %s\n", name, shorthand, description, defaultVal, required))
	}

	c.NonInheritedFlags().VisitAll(writeFlag)
	c.InheritedFlags().VisitAll(writeFlag)

	b.WriteString("|}\n")
	return b.String()
}

type byName []*cobra.Command

func (s byName) Len() int           { return len(s) }
func (s byName) Swap(i, j int)      { s[i], s[j] = s[j], s[i] }
func (s byName) Less(i, j int) bool { return s[i].Name() < s[j].Name() }

// genMenuCommands writes Chameleon Menu component entries for a command and
// its children, following the Cobra command hierarchy.
func genMenuCommands(b *strings.Builder, c *cobra.Command, depth int) {
	prefix := strings.Repeat("*", depth)
	children := c.Commands()
	sort.Sort(byName(children))
	for _, child := range children {
		if !child.IsAvailableCommand() || child.IsAdditionalHelpTopicCommand() {
			continue
		}
		link := referencePrefix + strings.ReplaceAll(child.CommandPath(), " ", "_")
		label := child.CommandPath()
		b.WriteString(fmt.Sprintf("%s %s | %s\n", prefix, link, label))
		genMenuCommands(b, child, depth+1)
	}
}

// --- MediaWiki API client ---

type mwClient struct {
	apiURL string
	http   *http.Client
}

func newMediaWikiClient(apiURL, user, pass string) (*mwClient, error) {
	jar, _ := cookiejar.New(nil)
	c := &mwClient{
		apiURL: apiURL,
		http:   &http.Client{Jar: jar},
	}

	// Step 1: get login token
	token, err := c.getToken("login")
	if err != nil {
		return nil, fmt.Errorf("getting login token: %w", err)
	}

	// Step 2: login
	resp, err := c.post(url.Values{
		"action":     {"login"},
		"lgname":     {user},
		"lgpassword": {pass},
		"lgtoken":    {token},
		"format":     {"json"},
	})
	if err != nil {
		return nil, fmt.Errorf("login request: %w", err)
	}

	var loginResp struct {
		Login struct {
			Result string `json:"result"`
		} `json:"login"`
	}
	if err := json.Unmarshal(resp, &loginResp); err != nil {
		return nil, fmt.Errorf("parsing login response: %w", err)
	}
	if loginResp.Login.Result != "Success" {
		return nil, fmt.Errorf("login failed: %s", loginResp.Login.Result)
	}

	return c, nil
}

func (c *mwClient) editPage(title, content, summary string) error {
	token, err := c.getToken("csrf")
	if err != nil {
		return fmt.Errorf("getting CSRF token: %w", err)
	}

	resp, err := c.post(url.Values{
		"action":  {"edit"},
		"title":   {title},
		"text":    {content},
		"summary": {summary},
		"token":   {token},
		"format":  {"json"},
	})
	if err != nil {
		return fmt.Errorf("edit request: %w", err)
	}

	var editResp struct {
		Edit struct {
			Result string `json:"result"`
		} `json:"edit"`
		Error *struct {
			Code string `json:"code"`
			Info string `json:"info"`
		} `json:"error"`
	}
	if err := json.Unmarshal(resp, &editResp); err != nil {
		return fmt.Errorf("parsing edit response: %w", err)
	}
	if editResp.Error != nil {
		return fmt.Errorf("API error: %s: %s", editResp.Error.Code, editResp.Error.Info)
	}
	if editResp.Edit.Result != "Success" {
		return fmt.Errorf("edit failed: %s", editResp.Edit.Result)
	}
	return nil
}

func (c *mwClient) getToken(tokenType string) (string, error) {
	u := c.apiURL + "?action=query&meta=tokens&type=" + tokenType + "&format=json"
	resp, err := c.http.Get(u)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	var tokenResp struct {
		Query struct {
			Tokens map[string]string `json:"tokens"`
		} `json:"query"`
	}
	if err := json.Unmarshal(body, &tokenResp); err != nil {
		return "", err
	}

	token, ok := tokenResp.Query.Tokens[tokenType+"token"]
	if !ok {
		return "", fmt.Errorf("token %q not found in response", tokenType+"token")
	}
	return token, nil
}

func (c *mwClient) post(values url.Values) ([]byte, error) {
	resp, err := c.http.PostForm(c.apiURL, values)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}
