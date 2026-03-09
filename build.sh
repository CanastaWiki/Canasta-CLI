#!/bin/sh -e

# Build script to populate the version information in Canasta.
# See https://www.digitalocean.com/community/tutorials/using-ldflags-to-set-version-information-for-go-applications
#
# Usage:
#   ./build.sh                           # Build for current platform
#   GOOS=linux GOARCH=amd64 ./build.sh   # Cross-compile for specific platform

# Read version from VERSION file if not set via environment
VERSION="${VERSION:-$(cat VERSION 2>/dev/null || echo "")}"

# version.Version gets v prefix (git tag format); DefaultImageTag gets bare version (image tag format)
IMAGE_TAG="${VERSION#v}"
CLI_VERSION="v${IMAGE_TAG}"

# Get version information
GIT_SHA=$(git rev-parse --short HEAD)
BUILD_TIME=$(date +'%Y-%m-%d %T')
LDFLAGS="-X 'github.com/CanastaWiki/Canasta-CLI/cmd/version.sha1=${GIT_SHA}' -X 'github.com/CanastaWiki/Canasta-CLI/cmd/version.buildTime=${BUILD_TIME}' -X 'github.com/CanastaWiki/Canasta-CLI/cmd/version.Version=${CLI_VERSION}' -X 'github.com/CanastaWiki/Canasta-CLI/internal/canasta.DefaultImageTag=${IMAGE_TAG}'"

# Default to current platform if GOOS/GOARCH not set
if [ -z "$GOOS" ]; then
  GOOS=$(go env GOOS)
fi

if [ -z "$GOARCH" ]; then
  GOARCH=$(go env GOARCH)
fi

# Create build directory if it doesn't exist
mkdir -p build

# Output filename
OUTPUT="build/canasta-${GOOS}-${GOARCH}"

go build -ldflags="${LDFLAGS}" -o "${OUTPUT}"
