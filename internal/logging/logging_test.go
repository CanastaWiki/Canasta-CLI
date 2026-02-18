package logging

import (
	"bytes"
	"log"
	"testing"
)

func TestSetVerboseTrue(t *testing.T) {
	SetVerbose(true)
	defer SetVerbose(false)
	if !GetVerbose() {
		t.Error("expected verbose to be true after SetVerbose(true)")
	}
}

func TestSetVerboseFalse(t *testing.T) {
	SetVerbose(true)
	SetVerbose(false)
	defer SetVerbose(false)
	if GetVerbose() {
		t.Error("expected verbose to be false after SetVerbose(false)")
	}
}

func TestGetVerboseDefaultIsFalse(t *testing.T) {
	// Reset verbose to its zero value
	SetVerbose(false)
	defer SetVerbose(false)
	if GetVerbose() {
		t.Error("expected default verbose to be false")
	}
}

func TestSetVerboseToggle(t *testing.T) {
	SetVerbose(false)
	defer SetVerbose(false)
	SetVerbose(true)
	if !GetVerbose() {
		t.Fatal("expected verbose=true")
	}
	SetVerbose(false)
	if GetVerbose() {
		t.Fatal("expected verbose=false after toggle")
	}
	SetVerbose(true)
	if !GetVerbose() {
		t.Fatal("expected verbose=true after second toggle")
	}
}

func TestPrintWhenVerboseIsTrue(t *testing.T) {
	var buf bytes.Buffer
	oldOutput := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(oldOutput)

	SetVerbose(true)
	defer SetVerbose(false)
	Print("hello verbose")
	if buf.Len() == 0 {
		t.Error("expected output when verbose is true, got nothing")
	}
	got := buf.String()
	if got != "hello verbose\n" {
		t.Errorf("expected %q, got %q", "hello verbose\n", got)
	}
}

func TestPrintWhenVerboseIsFalse(t *testing.T) {
	var buf bytes.Buffer
	oldOutput := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(oldOutput)

	SetVerbose(false)
	defer SetVerbose(false)
	Print("should not appear")

	if buf.Len() != 0 {
		t.Errorf("expected no output when verbose is false, got %q", buf.String())
	}
}

func TestPrintEmptyString(t *testing.T) {
	var buf bytes.Buffer
	oldOutput := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(oldOutput)

	SetVerbose(true)
	defer SetVerbose(false)
	Print("")

	got := buf.String()
	// log.Print("") still prints a newline
	if got != "\n" {
		t.Errorf("expected %q for empty Print, got %q", "\n", got)
	}
}

func TestPrintMultipleCalls(t *testing.T) {
	var buf bytes.Buffer
	oldOutput := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(oldOutput)

	SetVerbose(true)
	defer SetVerbose(false)
	Print("line1")
	Print("line2")

	got := buf.String()
	expected := "line1\nline2\n"
	if got != expected {
		t.Errorf("expected %q, got %q", expected, got)
	}
}

func TestPrintClearsLogFlags(t *testing.T) {
	// Capture and restore original flags
	originalFlags := log.Flags()
	defer log.SetFlags(originalFlags)

	// Set flags to something non-zero first
	log.SetFlags(log.Ldate | log.Ltime)

	var buf bytes.Buffer

	oldOutput := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(oldOutput)

	SetVerbose(true)
	defer SetVerbose(false)
	Print("test")

	// After Print, flags should be 0
	if log.Flags() != 0 {
		t.Errorf("expected log flags to be 0 after Print, got %d", log.Flags())
	}
}