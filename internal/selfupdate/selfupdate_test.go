package selfupdate

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// startMockServer spins up an httptest server that responds with the given
// status code and body, points  at it, and returns a cleanup func.
func startMockServer(t *testing.T, statusCode int, body string) func() {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(statusCode)
		_, _ = w.Write([]byte(body))
	}))

	// Override the package-level variable so getLatestVersion() hits our server.
	original := githubAPIURL
	githubAPIURL = srv.URL

	return func() {
		srv.Close()
		githubAPIURL = original
	}
}

// TestGetLatestVersion_ValidResponse verifies that a well-formed GitHub API
// response is parsed and the tag name is returned correctly.
func TestGetLatestVersion_ValidResponse(t *testing.T) {
	cleanup := startMockServer(t, http.StatusOK, `{"tag_name":"v1.58.0"}`)
	defer cleanup()

	got, err := getLatestVersion()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "v1.58.0" {
		t.Errorf("expected tag %q, got %q", "v1.58.0", got)
	}
}

// TestGetLatestVersion_EmptyTagName verifies that a response whose tag_name
// field is an empty string is treated as an error.
func TestGetLatestVersion_EmptyTagName(t *testing.T) {
	cleanup := startMockServer(t, http.StatusOK, `{"tag_name":""}`)
	defer cleanup()

	_, err := getLatestVersion()
	if err == nil {
		t.Fatal("expected an error for empty tag_name, got nil")
	}
	if !strings.Contains(err.Error(), "no tag_name") {
		t.Errorf("unexpected error message: %v", err)
	}
}

// TestGetLatestVersion_Non200Status verifies that any non-200 HTTP status code
// causes getLatestVersion to return an error.
func TestGetLatestVersion_Non200Status(t *testing.T) {
	cleanup := startMockServer(t, http.StatusInternalServerError, `{"message":"Server Error"}`)
	defer cleanup()

	_, err := getLatestVersion()
	if err == nil {
		t.Fatal("expected an error for non-200 status, got nil")
	}
	if !strings.Contains(err.Error(), "500") {
		t.Errorf("expected error to mention status 500, got: %v", err)
	}
}

// TestGetLatestVersion_InvalidJSON verifies that a response body that is not
// valid JSON is treated as an error.
func TestGetLatestVersion_InvalidJSON(t *testing.T) {
	cleanup := startMockServer(t, http.StatusOK, `not-valid-json`)
	defer cleanup()

	_, err := getLatestVersion()
	if err == nil {
		t.Fatal("expected an error for invalid JSON, got nil")
	}
	if !strings.Contains(err.Error(), "parse") {
		t.Errorf("expected error to mention parsing, got: %v", err)
	}
}
