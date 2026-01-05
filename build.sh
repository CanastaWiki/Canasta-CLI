#!/bin/sh -e

# Build script to populate the version information in Canasta.
# See https://www.digitalocean.com/community/tutorials/using-ldflags-to-set-version-information-for-go-applications
#
# Usage:
#   ./build.sh                           # Build for current platform
#   GOOS=linux GOARCH=amd64 ./build.sh   # Cross-compile for specific platform

# Get version information
GIT_SHA=$(git rev-parse --short HEAD)
BUILD_TIME=$(date +'%Y-%m-%d %T')
LDFLAGS="-X 'github.com/CanastaWiki/Canasta-CLI/cmd/version.sha1=${GIT_SHA}' -X 'github.com/CanastaWiki/Canasta-CLI/cmd/version.buildTime=${BUILD_TIME}'"

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

echo "Building Canasta CLI for ${GOOS}/${GOARCH}..."
go build -ldflags="${LDFLAGS}" -o "${OUTPUT}"
echo "Build complete: ${OUTPUT}"
