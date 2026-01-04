#!/bin/sh -e

# Build script to populate the version information in Canasta.
# See https://www.digitalocean.com/community/tutorials/using-ldflags-to-set-version-information-for-go-applications

# Get version information
GIT_SHA=$(git rev-parse --short HEAD)
BUILD_TIME=$(date +'%Y-%m-%d %T')
LDFLAGS="-X 'github.com/CanastaWiki/Canasta-CLI-Go/cmd/version.sha1=${GIT_SHA}' -X 'github.com/CanastaWiki/Canasta-CLI-Go/cmd/version.buildTime=${BUILD_TIME}'"

# Create build directory if it doesn't exist
mkdir -p build

echo "Building Canasta CLI for multiple platforms..."

# Build for Linux amd64
echo "Building for Linux (amd64)..."
GOOS=linux GOARCH=amd64 go build -ldflags="${LDFLAGS}" -o build/canasta-linux-amd64

# Build for Linux arm64
echo "Building for Linux (arm64)..."
GOOS=linux GOARCH=arm64 go build -ldflags="${LDFLAGS}" -o build/canasta-linux-arm64

# Build for macOS amd64 (Intel)
echo "Building for macOS (amd64/Intel)..."
GOOS=darwin GOARCH=amd64 go build -ldflags="${LDFLAGS}" -o build/canasta-darwin-amd64

# Build for macOS arm64 (Apple Silicon)
echo "Building for macOS (arm64/Apple Silicon)..."
GOOS=darwin GOARCH=arm64 go build -ldflags="${LDFLAGS}" -o build/canasta-darwin-arm64

echo "Build complete! Binaries are in the build/ directory:"
ls -lh build/
