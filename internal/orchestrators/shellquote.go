package orchestrators

import "strings"

// ShellQuote wraps a value in single quotes for safe use in a shell command
// string passed to bash -c (e.g., via ExecWithError). Within single quotes,
// bash does not expand variables, backticks, or other metacharacters. The only
// character that cannot appear inside single quotes is a single quote itself,
// which is handled by ending the quoted string, inserting an escaped single
// quote, and reopening the quoted string: ' → '\''
func ShellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
