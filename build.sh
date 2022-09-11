#!/bin/sh -e

# Build script to populate the version information in Canasta.
# See https://www.digitalocean.com/community/tutorials/using-ldflags-to-set-version-information-for-go-applications

go build -ldflags="-X 'github.com/CanastaWiki/Canasta-CLI-Go/cmd/version.sha1=$(git rev-parse --short HEAD)'
    -X 'github.com/CanastaWiki/Canasta-CLI-Go/cmd/version.buildTime=$(date +'%Y-%m-%d %T')'"
