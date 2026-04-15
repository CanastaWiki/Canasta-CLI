//go:build integration

package integration

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
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

// waitForExtensionState polls siteinfo until the named extension matches
// the expected state (present or absent), or times out.
func waitForExtensionState(t *testing.T, httpPort, name string, wantPresent bool, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		info := querySiteInfo(t, httpPort, "extensions", 1)
		if hasExtension(info, name) == wantPresent {
			return
		}
		time.Sleep(5 * time.Second)
	}
	state := "enabled"
	if !wantPresent {
		state = "disabled"
	}
	t.Errorf("expected %s extension to be %s, but it was not after %v", name, state, timeout)
}

// waitForSkinState polls siteinfo until the named skin matches the
// expected state (present or absent), or times out.
func waitForSkinState(t *testing.T, httpPort, name string, wantPresent bool, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		info := querySiteInfo(t, httpPort, "skins", 1)
		if hasSkin(info, name) == wantPresent {
			return
		}
		time.Sleep(5 * time.Second)
	}
	state := "enabled"
	if !wantPresent {
		state = "disabled"
	}
	t.Errorf("expected %s skin to be %s, but it was not after %v", name, state, timeout)
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

	// Grant anonymous read so the siteinfo API can be queried without auth.
	// Files in config/settings/global/ are loaded after CanastaDefaultSettings,
	// in lexicographic order, so the "zz-" prefix ensures this runs last.
	globalDir := filepath.Join(inst.WorkDir, inst.ID, "config", "settings", "global")
	if err := os.MkdirAll(globalDir, 0755); err != nil {
		t.Fatalf("failed to create global settings dir: %v", err)
	}
	publicPHP := filepath.Join(globalDir, "zz-test-public.php")
	if err := os.WriteFile(publicPHP, []byte("<?php\n$wgGroupPermissions['*']['read'] = true;\n"), 0644); err != nil {
		t.Fatalf("failed to write public settings file: %v", err)
	}

	// Wait for the wiki to be accessible
	waitForWiki(t, inst.HTTPPort, 5*time.Minute)

	// --- Extension enable ---
	out, err = inst.run(t, "extension", "enable", "-i", inst.ID, "Cite")
	if err != nil {
		t.Fatalf("canasta extension enable failed: %v\n%s", err, out)
	}
	waitForExtensionState(t, inst.HTTPPort, "Cite", true, 2*time.Minute)

	// --- Extension disable ---
	out, err = inst.run(t, "extension", "disable", "-i", inst.ID, "Cite")
	if err != nil {
		t.Fatalf("canasta extension disable failed: %v\n%s", err, out)
	}
	waitForExtensionState(t, inst.HTTPPort, "Cite", false, 2*time.Minute)

	// --- Skin enable ---
	out, err = inst.run(t, "skin", "enable", "-i", inst.ID, "Timeless")
	if err != nil {
		t.Fatalf("canasta skin enable failed: %v\n%s", err, out)
	}
	waitForSkinState(t, inst.HTTPPort, "timeless", true, 2*time.Minute)

	// --- Skin disable ---
	out, err = inst.run(t, "skin", "disable", "-i", inst.ID, "Timeless")
	if err != nil {
		t.Fatalf("canasta skin disable failed: %v\n%s", err, out)
	}
	waitForSkinState(t, inst.HTTPPort, "timeless", false, 2*time.Minute)
}
