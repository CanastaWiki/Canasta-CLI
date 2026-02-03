#!/bin/bash

# Canasta CLI installer

# This script downloads and installs the Canasta command-line interface (CLI), which is
# an executable called "canasta".

PROGNAME=$(basename "$0")
VERSION="latest"
TARGET="/usr/local/bin/canasta"
SKIP_CHECKS=false

usage() {
  cat << EOF >&2
Usage: $PROGNAME [-v <ver>|--version <ver>] [-l|--list] [-t <path>|--target <path>] [-s|--skip-checks]
-v <ver>, --version <ver>   : install specific version
-l, --list                  : list all available versions
-t <path>, --target <path>  : install binary to <path> (default: /usr/local/bin/canasta)
-s, --skip-checks           : skip dependency checks (git, docker, docker compose)
       *                    : usage
EOF
  exit 1
}

list_versions() {
  echo "Fetching available versions..."
  releases=$(wget -qO- "https://api.github.com/repos/CanastaWiki/Canasta-CLI/releases")
  versions=$(echo "$releases" | grep -Po '"tag_name": "\K.*?(?=")')

  echo "Available versions:"
  echo "$versions"
  exit 0
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -v|--version)
        if [[ -z "$2" || $2 == -* ]]; then
          echo "No version number provided after '-v' flag."
          VERSION=""
          shift
        else
          VERSION="$2"
          shift 2
        fi
        ;;
      -l|--list)
        list_versions
        ;;
      -t|--target)
        TARGET="$2"
        shift 2
        ;;
      -s|--skip-checks)
        SKIP_CHECKS=true
        shift
        ;;
      *)
        usage
        ;;
    esac
  done
}

validate_version() {
  local version_to_validate="$1"
  if [[ $version_to_validate =~ ^(([0-9]{1,3}[\.]){2}[0-9]{1,3}).* ]]; then
    return 0
  else
    echo "Invalid version number!"
    return 1
  fi
}

choose_version() {
  echo "-----"
  echo "Checking the version..."

  if [[ -z ${VERSION-} ]] || [ $VERSION == "latest" ] || ! validate_version "$VERSION"; then
    while true; do
      read -rp "Enter a valid version number (e.g., 1.31.0), or hit ENTER for the latest version: " -e VERSION

      if [[ $VERSION =~ ^(([0-9]{1,3}[\.]){2}[0-9]{1,3}).* ]]; then
        echo "Installing version $VERSION."
        break
      elif [[ -z $VERSION ]]; then
        VERSION="latest"
        echo "No version was specified; installing the latest version."
        break
      else
        echo "Invalid version number. Please try again."
      fi
    done
  else
    echo "Version $VERSION has already been specified; proceeding with installation."
  fi
}


check_dependencies() {
  local dependencies=("wget" "git" "docker")
  local not_found=()

  echo "Checking dependencies..."
  for dep in "${dependencies[@]}"; do
    if ! command -v "$dep" >/dev/null 2>&1; then
      not_found+=("$dep")
    fi
  done

  # Ensure that Docker Compose V2 (i.e., "docker compose") is installed;
  # Docker Compose V1 (i.e., "docker-compose") is deprecated as of July 2023.
  if ! docker compose version >/dev/null 2>&1; then
    not_found+=("Docker Compose V2 (see https://docs.docker.com/compose/install/)")
  fi

  if [ "${#not_found[@]}" -ne 0 ]; then
    echo "The following dependencies are missing:"
    for missing in "${not_found[@]}"; do
      echo "- $missing"
    done
    echo "Please install the missing dependencies before continuing."
    exit 1
  else
    echo "All the required dependencies are found."
  fi
}

check_wget_show_progress() {
  # The show-progress param was added to wget in version 1.16 (October 2014).
  wgetOptions=$(wget --help)
  if [[ $wgetOptions == *"show-progress"* ]]; then
    WGET_SHOW_PROGRESS="--show-progress"
  else
    WGET_SHOW_PROGRESS=""
  fi
}

detect_platform() {
  # Detect OS
  local os=""
  case "$(uname -s)" in
    Linux*)
      os="linux"
      ;;
    Darwin*)
      os="darwin"
      ;;
    *)
      echo "Unsupported operating system: $(uname -s)"
      echo "Canasta CLI supports Linux and macOS only."
      exit 1
      ;;
  esac

  # Detect architecture
  local arch=""
  case "$(uname -m)" in
    x86_64|amd64)
      arch="amd64"
      ;;
    aarch64|arm64)
      arch="arm64"
      ;;
    *)
      echo "Unsupported architecture: $(uname -m)"
      echo "Canasta CLI supports AMD64 (x86-64) and ARM64 (AArch64) only."
      exit 1
      ;;
  esac

  echo "${os}-${arch}"
}

download_and_install() {
  local canasta_url=""
  local platform=""
  local binary_name=""

  check_wget_show_progress

  # Detect platform
  platform=$(detect_platform)
  binary_name="canasta-${platform}"

  echo "Detected platform: $platform"

  if [ "$VERSION" == "latest" ]; then
    canasta_url="https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/${binary_name}"
  else
    canasta_url="https://github.com/CanastaWiki/Canasta-CLI/releases/download/v${VERSION}/${binary_name}"
  fi

  echo "Downloading Canasta CLI version $VERSION for $platform..."
  if ! sudo wget -q $WGET_SHOW_PROGRESS "$canasta_url" -O canasta; then
    echo "Platform-specific binary not found. Trying generic binary (backward compatibility)..."

    # Fall back to generic binary name for older releases
    if [ "$VERSION" == "latest" ]; then
      canasta_url="https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta"
    else
      canasta_url="https://github.com/CanastaWiki/Canasta-CLI/releases/download/v${VERSION}/canasta"
    fi

    if ! sudo wget -q $WGET_SHOW_PROGRESS "$canasta_url" -O canasta; then
      echo "Download failed. The version you specified might not exist."
      echo "Please use '-l' or '--list' flag to see the available versions or try again."
      rm -f canasta   # Delete the 0-byte canasta file
      exit 1
    fi
  fi
  echo "Download was successful; now installing Canasta CLI."
  sudo chmod u=rwx,g=xr,o=x canasta
  sudo mv canasta "$TARGET"
  if [ $? -ne 0 ]; then
    echo "Installation failed. Please try again."

    while true; do
      read -rp "Do you want to keep the downloaded file? (y/n): " yn
      case $yn in
        [Yy]* ) break;;
        [Nn]* ) rm -f canasta; echo "Downloaded file deleted."; break;;
        * ) echo "Please answer yes or no.";;
      esac
    done
    exit 1
  fi
  echo "Canasta CLI was successfully installed."
}

main() {
  parse_arguments "$@"
  if [ "$SKIP_CHECKS" = false ]; then
    check_dependencies
  fi
  choose_version
  download_and_install
  if [ "$SKIP_CHECKS" = false ]; then
    echo "Please make sure you have a working kubectl if you wish to use Kubernetes as an orchestrator."
    echo -e "\nUsage: sudo canasta [COMMAND] [ARGUMENTS...]"
  fi
}
main "$@"
