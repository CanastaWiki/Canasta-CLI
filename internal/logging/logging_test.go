package logging

import (
	"bytes"
	"log"
	"testing"
)

// capturePrintOutput sets up log capture and state cleanup, calls fn,
// then returns the captured output. All global state (log output, log
// flags, verbose) is restored after fn returns.
func capturePrintOutput(t *testing.T, verbose bool, fn func()) string {
	t.Helper()

	originalFlags := log.Flags()
	defer log.SetFlags(originalFlags)

	var buf bytes.Buffer
	oldOutput := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(oldOutput)

	SetVerbose(verbose)
	defer SetVerbose(false)

	fn()
	return buf.String()
}

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
	got := capturePrintOutput(t, true, func() {
		Print("hello verbose")
	})
	if got == "" {
		t.Error("expected output when verbose is true, got nothing")
	}
	if got != "hello verbose\n" {
		t.Errorf("expected %q, got %q", "hello verbose\n", got)
	}
}

func TestPrintWhenVerboseIsFalse(t *testing.T) {
	got := capturePrintOutput(t, false, func() {
		Print("should not appear")
	})
	if got != "" {
		t.Errorf("expected no output when verbose is false, got %q", got)
	}
}

func TestPrintEmptyString(t *testing.T) {
	got := capturePrintOutput(t, true, func() {
		Print("")
	})
	// log.Print("") still prints a newline
	if got != "\n" {
		t.Errorf("expected %q for empty Print, got %q", "\n", got)
	}
}

func TestPrintMultipleCalls(t *testing.T) {
	got := capturePrintOutput(t, true, func() {
		Print("line1")
		Print("line2")
	})
	expected := "line1\nline2\n"
	if got != expected {
		t.Errorf("expected %q, got %q", expected, got)
	}
}

func TestPrintClearsLogFlags(t *testing.T) {
	// Set flags to something non-zero before calling Print
	originalFlags := log.Flags()
	defer log.SetFlags(originalFlags)
	log.SetFlags(log.Ldate | log.Ltime)

	got := capturePrintOutput(t, true, func() {
		Print("test")
	})

	// Ensure the emitted log line has no date/time prefix,
	// even though flags were non-zero before the call.
	expected := "test\n"
	if got != expected {
		t.Errorf("expected %q without date/time prefix, got %q", expected, got)
	}
}
