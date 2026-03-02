package version

import (
    "testing"
)

func setVersionVars(v, s, b string) func() {
    origVersion, origSha1, origBuildTime := Version, sha1, buildTime
    Version = v
    sha1 = s
    buildTime = b
    return func() {
        Version = origVersion
        sha1 = origSha1
        buildTime = origBuildTime
    }
}

func TestDisplayVersion(t *testing.T) {
    tests := []struct {
        name      string
        version   string
        sha1      string
        buildTime string
        want      string
    }{
        {
            name:      "release build",
            version:   "v1.52.0",
            sha1:      "abc1234",
            buildTime: "2025-06-15T12:00:00Z",
            want:      "Canasta CLI v1.52.0 (commit abc1234, built 2025-06-15T12:00:00Z)",
        },
        {
            name:      "dev build with empty version",
            version:   "",
            sha1:      "def5678",
            buildTime: "2025-01-01T00:00:00Z",
            want:      "Canasta CLI dev (commit def5678, built 2025-01-01T00:00:00Z)",
        },
        {
            name:      "all fields empty",
            version:   "",
            sha1:      "",
            buildTime: "",
            want:      "Canasta CLI dev (commit , built )",
        },
        {
            name:      "only commit set",
            version:   "",
            sha1:      "face000",
            buildTime: "",
            want:      "Canasta CLI dev (commit face000, built )",
        },
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            defer setVersionVars(tt.version, tt.sha1, tt.buildTime)()

            got := DisplayVersion()
            if got != tt.want {
                t.Errorf("DisplayVersion() = %q, want %q", got, tt.want)
            }
        })
    }
}