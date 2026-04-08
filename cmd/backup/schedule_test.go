package backup

import "testing"

func TestValidateCron(t *testing.T) {
	tests := []struct {
		name    string
		cron    string
		wantErr bool
	}{
		{"valid daily", "0 2 * * *", false},
		{"valid hourly", "0 * * * *", false},
		{"valid specific", "30 4 1 1 0", false},
		{"too few fields", "0 2 * *", true},
		{"too many fields", "0 2 * * * *", true},
		{"empty string", "", true},
		{"invalid character", "0 2 * * MON", true},
		{"special character @", "@ daily", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateCron(tt.cron)
			if (err != nil) != tt.wantErr {
				t.Errorf("validateCron(%q) error = %v, wantErr %v", tt.cron, err, tt.wantErr)
			}
		})
	}
}

func TestCronFromLine(t *testing.T) {
	tests := []struct {
		name string
		line string
		want string
	}{
		{"full crontab line", "0 2 * * * /usr/local/bin/canasta backup create -i wiki --tag test", "0 2 * * *"},
		{"just cron fields", "0 2 * * *", "0 2 * * *"},
		{"short line", "foo bar", "foo bar"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := cronFromLine(tt.line)
			if got != tt.want {
				t.Errorf("cronFromLine(%q) = %q, want %q", tt.line, got, tt.want)
			}
		})
	}
}

func TestValidateCronEdgeCases(t *testing.T) {
	tests := []struct {
		name    string
		cron    string
		wantErr bool
	}{
		{"ranges", "0-30 * * * *", false},
		{"lists", "0,15,30,45 * * * *", false},
		{"steps", "*/5 * * * *", false},
		{"combined", "0-30/5 1,2,3 * * *", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateCron(tt.cron)
			if (err != nil) != tt.wantErr {
				t.Errorf("validateCron(%q) error = %v, wantErr %v", tt.cron, err, tt.wantErr)
			}
		})
	}
}

func TestPurgeFromLine(t *testing.T) {
	tests := []struct {
		name string
		line string
		want string
	}{
		{
			name: "unquoted value",
			line: "0 2 * * * /usr/local/bin/canasta backup create -i wiki && canasta backup purge -i wiki --older-than 30d >> backup.log 2>&1",
			want: "30d",
		},
		{
			name: "double-quoted value",
			line: `0 2 * * * /usr/local/bin/canasta backup create -i wiki && canasta backup purge -i wiki --older-than "30d" >> backup.log 2>&1`,
			want: "30d",
		},
		{
			name: "single-quoted value",
			line: "0 2 * * * /usr/local/bin/canasta backup create -i wiki && canasta backup purge -i wiki --older-than '30d' >> backup.log 2>&1",
			want: "30d",
		},
		{
			name: "no flag present",
			line: "0 2 * * * /usr/local/bin/canasta backup create -i wiki --tag scheduled >> backup.log 2>&1",
			want: "",
		},
		{
			name: "multiple flags including older-than",
			line: "0 2 * * * /usr/local/bin/canasta backup create -i wiki --tag scheduled && canasta backup purge -i wiki --older-than 6m --verbose >> backup.log 2>&1",
			want: "6m",
		},
		{
			name: "empty line",
			line: "",
			want: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := purgeFromLine(tt.line)
			if got != tt.want {
				t.Errorf("purgeFromLine(%q) = %q, want %q", tt.line, got, tt.want)
			}
		})
	}
}
