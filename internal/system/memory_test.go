package system

import (
	"errors"
	"strings"
	"testing"
)

func TestParseLinuxMemAvailable(t *testing.T) {
	tests := []struct {
		name    string
		input   string
		want    uint64
		wantErr bool
	}{
		{
			name: "typical meminfo",
			input: "MemTotal:       16384000 kB\n" +
				"MemFree:         8192000 kB\n" +
				"MemAvailable:    8000000 kB\n" +
				"Buffers:          512000 kB\n",
			want:    8000000 * 1024,
			wantErr: false,
		},
		{
			name:    "MemAvailable is the first line",
			input:   "MemAvailable:    4096 kB\n",
			want:    4096 * 1024,
			wantErr: false,
		},
		{
			name:    "zero available memory",
			input:   "MemTotal:       16384000 kB\nMemAvailable:    0 kB\n",
			want:    0,
			wantErr: false,
		},
		{
			name:    "MemAvailable not present",
			input:   "MemTotal:       16384000 kB\nMemFree:         8192000 kB\n",
			want:    0,
			wantErr: true,
		},
		{
			name:    "empty input",
			input:   "",
			want:    0,
			wantErr: true,
		},
		{
			name:    "malformed MemAvailable line",
			input:   "MemAvailable:\n",
			want:    0,
			wantErr: true,
		},
		{
			name:    "non-numeric MemAvailable value",
			input:   "MemAvailable: abc kB\n",
			want:    0,
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := parseLinuxMemAvailable(strings.NewReader(tt.input))
			if (err != nil) != tt.wantErr {
				t.Errorf("parseLinuxMemAvailable() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if got != tt.want {
				t.Errorf("parseLinuxMemAvailable() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestGbToByte(t *testing.T) {
	tests := []struct {
		name string
		gb   int
		want uint64
	}{
		{name: "zero", gb: 0, want: 0},
		{name: "one GB", gb: 1, want: 1 * 1024 * 1024 * 1024},
		{name: "four GB", gb: 4, want: 4 * 1024 * 1024 * 1024},
		{name: "negative", gb: -1, want: 0},
		{name: "large value", gb: 16, want: 16 * 1024 * 1024 * 1024},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := gbToByte(tt.gb); got != tt.want {
				t.Errorf("gbToByte(%d) = %v, want %v", tt.gb, got, tt.want)
			}
		})
	}
}

func TestByteToGB(t *testing.T) {
	tests := []struct {
		name string
		b    uint64
		want float64
	}{
		{name: "zero", b: 0, want: 0.0},
		{name: "one GB", b: 1 * 1024 * 1024 * 1024, want: 1.0},
		{name: "four GB", b: 4 * 1024 * 1024 * 1024, want: 4.0},
		{name: "half GB", b: 512 * 1024 * 1024, want: 0.5},
		{name: "1.5 GB", b: 1536 * 1024 * 1024, want: 1.5},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := byteToGB(tt.b); got != tt.want {
				t.Errorf("byteToGB(%v) = %v, want %v", tt.b, got, tt.want)
			}
		})
	}
}

func TestCheckMemoryInGBWithGetter(t *testing.T) {
	const oneGB = uint64(1 * 1024 * 1024 * 1024)

	tests := []struct {
		name    string
		minGB   int
		getter  func() (uint64, error)
		wantErr bool
	}{
		{
			name:  "sufficient memory",
			minGB: 4,
			getter: func() (uint64, error) {
				return 8 * oneGB, nil
			},
			wantErr: false,
		},
		{
			name:  "exactly the minimum",
			minGB: 4,
			getter: func() (uint64, error) {
				return 4 * oneGB, nil
			},
			wantErr: false,
		},
		{
			name:  "insufficient memory",
			minGB: 4,
			getter: func() (uint64, error) {
				return 2 * oneGB, nil
			},
			wantErr: true,
		},
		{
			name:  "getter returns zero (unknown platform)",
			minGB: 999,
			getter: func() (uint64, error) {
				return 0, nil
			},
			wantErr: false,
		},
		{
			name:  "getter returns error",
			minGB: 4,
			getter: func() (uint64, error) {
				return 0, errors.New("memory read failed")
			},
			wantErr: true,
		},
		{
			name:  "minGB is zero",
			minGB: 0,
			getter: func() (uint64, error) {
				return 1 * oneGB, nil
			},
			wantErr: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := checkMemoryInGBWithGetter(tt.minGB, tt.getter)
			if (err != nil) != tt.wantErr {
				t.Errorf("checkMemoryInGBWithGetter(%d, ...) error = %v, wantErr %v", tt.minGB, err, tt.wantErr)
			}
		})
	}
}
