package selfupdate

import (
	"strings"
	"testing"
)

// TestParseLatestVersion_ValidResponse verifies that a well-formed GitHub API
// response is parsed and the tag name is returned correctly.
func TestParseLatestVersion_ValidResponse(t *testing.T) {
	got, err := parseLatestVersion(strings.NewReader(`{"tag_name":"v1.58.0"}`))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "v1.58.0" {
		t.Errorf("expected tag %q, got %q", "v1.58.0", got)
	}
}

// TestParseLatestVersion_EmptyTagName verifies that a response whose tag_name
// field is an empty string is treated as an error.
func TestParseLatestVersion_EmptyTagName(t *testing.T) {
	_, err := parseLatestVersion(strings.NewReader(`{"tag_name":""}`))
	if err == nil {
		t.Fatal("expected an error for empty tag_name, got nil")
	}
	if !strings.Contains(err.Error(), "no tag_name") {
		t.Errorf("unexpected error message: %v", err)
	}
}

// TestParseLatestVersion_MissingTagName verifies that a response without a
// tag_name field is treated as an error.
func TestParseLatestVersion_MissingTagName(t *testing.T) {
	_, err := parseLatestVersion(strings.NewReader(`{"name":"v1.58.0"}`))
	if err == nil {
		t.Fatal("expected an error for missing tag_name, got nil")
	}
	if !strings.Contains(err.Error(), "no tag_name") {
		t.Errorf("unexpected error message: %v", err)
	}
}

// TestParseLatestVersion_InvalidJSON verifies that a response body that is not
// valid JSON is treated as an error.
func TestParseLatestVersion_InvalidJSON(t *testing.T) {
	_, err := parseLatestVersion(strings.NewReader(`not-valid-json`))
	if err == nil {
		t.Fatal("expected an error for invalid JSON, got nil")
	}
	if !strings.Contains(err.Error(), "parse") {
		t.Errorf("expected error to mention parsing, got: %v", err)
	}
}
