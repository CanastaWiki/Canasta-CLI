package add

import (
	"fmt"
	urlpkg "net/url"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
)

// ParsedWikiURL holds the domain and path extracted from a wiki URL string.
type ParsedWikiURL struct {
	Domain string
	Path   string
}

// ParseWikiURL splits a domain/path URL string (e.g. "localhost/wiki2" or
// "example.com/docs") into its domain and path components. If no scheme is
// present, "https://" is prepended before parsing. The path is returned
// without leading or trailing slashes.
func ParseWikiURL(rawURL string) (ParsedWikiURL, error) {
	urlString := rawURL
	if !strings.HasPrefix(urlString, "http://") && !strings.HasPrefix(urlString, "https://") {
		urlString = "https://" + urlString
	}
	parsed, err := urlpkg.Parse(urlString)
	if err != nil {
		return ParsedWikiURL{}, fmt.Errorf("failed to parse URL: %w", err)
	}
	return ParsedWikiURL{
		Domain: parsed.Host,
		Path:   strings.Trim(parsed.Path, "/"),
	}, nil
}

// resolveFilePaths converts each non-empty relative path to an absolute path
// relative to baseDir.
func resolveFilePaths(baseDir string, paths ...*string) {
	canasta.ResolveFilePaths(baseDir, paths...)
}
