package logging

import (
	"bytes"
	"log"
	"testing"
)

func TestSetVerbose_True(t *testing.T) {
	SetVerbose(true)
	if !GetVerbose() {
		t.Error("expected verbose to be true after SetVerbose(true)")
	}
}

func TestSetVerbose_False(t *testing.T) {
	SetVerbose(false)
	if GetVerbose() {
		t.Error("expected verbose to be false after SetVerbose(false)")
	}
}

func TestGetVerbose_DefaultIsFalse(t *testing.T) {
	// Reset verbose to its zero value
	if GetVerbose() {
		t.Error("expected default verbose to be false")
	}
}

func TestSetVerbose_Toggle(t *testing.T) {
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

func TestPrint_WhenVerboseIsTrue(t *testing.T) {
	var buf bytes.Buffer
	oldOutput := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(oldOutput)

	SetVerbose(true)
	Print("hello verbose")
	if buf.Len() == 0 {
		t.Error("expected output when verbose is true, got nothing")
	}
	got := buf.String()
	if got != "hello verbose\n" {
		t.Errorf("expected %q, got %q", "hello verbose\n", got)
	}
}

func TestPrint_WhenVerboseIsFalse(t *testing.T) {
	var buf bytes.Buffer
	oldOutput := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(oldOutput)

	SetVerbose(false)
	Print("should not appear")

	if buf.Len() != 0 {
		t.Errorf("expected no output when verbose is false, got %q", buf.String())
	}
}

func TestPrint_EmptyString(t *testing.T) {
	var buf bytes.Buffer
	log.SetOutput(&buf)
	defer log.SetOutput(nil)

	SetVerbose(true)
	Print("")

	got := buf.String()
	// log.Print("") still prints a newline
	if got != "\n" {
		t.Errorf("expected %q for empty Print, got %q", "\n", got)
	}
}

func TestPrint_MultipleCalls(t *testing.T) {
	var buf bytes.Buffer
	log.SetOutput(&buf)
	defer log.SetOutput(nil)

	SetVerbose(true)
	Print("line1")
	Print("line2")

	got := buf.String()
	expected := "line1\nline2\n"
	if got != expected {
		t.Errorf("expected %q, got %q", expected, got)
	}
}

func TestPrint_ClearsLogFlags(t *testing.T) {
	// Set flags to something non-zero first
	log.SetFlags(log.Ldate | log.Ltime)

	SetVerbose(true)

	var buf bytes.Buffer
	log.SetOutput(&buf)
	defer log.SetOutput(nil)

	Print("test")

	// After Print, flags should be 0
	if log.Flags() != 0 {
		t.Errorf("expected log flags to be 0 after Print, got %d", log.Flags())
	}
}
