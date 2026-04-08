//go:build integration

package integration

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

// siteInfoResponse is used to unmarshal the MediaWiki siteinfo API response
// for extensions and skins queries.
type siteInfoResponse struct {
	Query struct {
		Extensions []struct {
			Name string `json:"name"`
		} `json:"extensions"`
		Skins []struct {
			Code string `json:"code"`
		} `json:"skins"`
	} `json:"query"`
}

// querySiteInfo fetches siteinfo from the MediaWiki API with the given siprop
// (e.g., "extensions" or "skins") and returns the parsed response. It retries
// up to maxRetries times with a delay between attempts to allow the wiki to
// reload after configuration changes.
func querySiteInfo(t *testing.T, httpPort, siprop string, maxRetries int) siteInfoResponse {
	t.Helper()
	apiURL := fmt.Sprintf("http://127.0.0.1:%s/w/api.php?action=query&meta=siteinfo&siprop=%s&format=json", httpPort, siprop)

	var result siteInfoResponse
	var lastErr error
	for i := 0; i < maxRetries; i++ {
		req, err := http.NewRequest("GET", apiURL, nil)
		if err != nil {
			t.Fatalf("failed to create request: %v", err)
		}
		req.Host = "localhost"
		client := &http.Client{Timeout: 10 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			lastErr = err
			time.Sleep(5 * time.Second)
			continue
		}
		decodeErr := json.NewDecoder(resp.Body).Decode(&result)
		resp.Body.Close()
		if decodeErr != nil {
			lastErr = decodeErr
			time.Sleep(5 * time.Second)
			continue
		}
		return result
	}
	t.Fatalf("failed to query siteinfo siprop=%s after %d retries: %v", siprop, maxRetries, lastErr)
	return result // unreachable
}

// hasExtension checks if the given extension name appears in the siteinfo response.
func hasExtension(info siteInfoResponse, name string) bool {
	for _, ext := range info.Query.Extensions {
		if ext.Name == name {
			return true
		}
	}
	return false
}

// hasSkin checks if the given skin code appears in the siteinfo response.
// Skin codes are lowercase (e.g., "timeless").
func hasSkin(info siteInfoResponse, code string) bool {
	code = strings.ToLower(code)
	for _, skin := range info.Query.Skins {
		if strings.ToLower(skin.Code) == code {
			return true
		}
	}
	return false
}

// TestExtensionSkin_EnableDisable creates an instance, then exercises the
// extension enable/disable and skin enable/disable commands, verifying each
// change via the MediaWiki siteinfo API.
func TestExtensionSkin_EnableDisable(t *testing.T) {
	inst := createTestInstance(t, "inttest-extskin")

	// Create the instance with the main wiki
	out, err := inst.run(t, "create",
		"-i", inst.ID,
		"-w", "main",
		"-n", "localhost",
		"-p", inst.WorkDir,
		"-e", inst.EnvFile,
	)
	if err != nil {
		t.Fatalf("canasta create failed: %v\n%s", err, out)
	}

	// Wait for the wiki to be accessible
	waitForWiki(t, inst.HTTPPort, 5*time.Minute)

	// --- Extension enable ---
	out, err = inst.run(t, "extension", "enable", "-i", inst.ID, "Cite")
	if err != nil {
		t.Fatalf("canasta extension enable failed: %v\n%s", err, out)
	}
	time.Sleep(5 * time.Second)

	info := querySiteInfo(t, inst.HTTPPort, "extensions", 6)
	if !hasExtension(info, "Cite") {
		t.Errorf("expected Cite extension to be enabled, but it was not found in siteinfo")
	}

	// --- Extension disable ---
	out, err = inst.run(t, "extension", "disable", "-i", inst.ID, "Cite")
	if err != nil {
		t.Fatalf("canasta extension disable failed: %v\n%s", err, out)
	}
	time.Sleep(5 * time.Second)

	info = querySiteInfo(t, inst.HTTPPort, "extensions", 6)
	if hasExtension(info, "Cite") {
		t.Errorf("expected Cite extension to be disabled, but it still appears in siteinfo")
	}

	// --- Skin enable ---
	out, err = inst.run(t, "skin", "enable", "-i", inst.ID, "Timeless")
	if err != nil {
		t.Fatalf("canasta skin enable failed: %v\n%s", err, out)
	}
	time.Sleep(5 * time.Second)

	info = querySiteInfo(t, inst.HTTPPort, "skins", 6)
	if !hasSkin(info, "timeless") {
		t.Errorf("expected Timeless skin to be enabled, but it was not found in siteinfo")
	}

	// --- Skin disable ---
	out, err = inst.run(t, "skin", "disable", "-i", inst.ID, "Timeless")
	if err != nil {
		t.Fatalf("canasta skin disable failed: %v\n%s", err, out)
	}
	time.Sleep(5 * time.Second)

	info = querySiteInfo(t, inst.HTTPPort, "skins", 6)
	if hasSkin(info, "timeless") {
		t.Errorf("expected Timeless skin to be disabled, but it still appears in siteinfo")
	}
}
