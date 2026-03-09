package permissions

import "io/fs"

const (
	// DirectoryPermission is the default permission mode for directories created by the CLI.
	DirectoryPermission fs.FileMode = 0755
	// FilePermission is the default permission mode for files created by the CLI.
	FilePermission fs.FileMode = 0644
	// SecretFilePermission is the permission mode for files containing secrets (owner read/write only).
	SecretFilePermission fs.FileMode = 0600
)
