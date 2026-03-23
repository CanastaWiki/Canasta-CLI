package defaults

import _ "embed"

// Gitignore is the default .gitignore for a gitops-managed instance.
//
//go:embed gitignore
var Gitignore string

// Gitattributes is the default .gitattributes for git-crypt encryption.
//
//go:embed gitattributes
var Gitattributes string
