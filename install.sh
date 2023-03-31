#!/bin/bash

# Canasta CLI installer

# This script downloads and installs the Canasta command-line interface (CLI), which is
# an executable called "canasta".
# It also checks for the presence of Git, Docker and Docker Compose, and displays a
# warning to the user if they are not correctly installed, although it installs the CLI
# regardless.

PROGNAME=$(basename "$0")
VERSION="latest"

usage() {
  cat << EOF >&2
Usage: $PROGNAME [-i|--interactive] [-v <ver>|--version <ver>] [-l|--list]
-v <ver>, --version <ver> : install specific version
-i, --interactive          : interactive version
-l, --list                 : list all available versions
       *                   : usage
EOF
  exit 1
}

list_versions() {
  wget --version >/dev/null 2>&1
  WGET_IS_AVAILABLE=$?

  if [ $WGET_IS_AVAILABLE -ne 0 ]; then
    echo "Error: wget is not found. Please install wget and try again."
    exit 1
  fi

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
      -i|--interactive)
        echo "interactive mode"
        INTERACTIVE=1
        shift
        ;;
      -v|--version)
        VERSION="$2"
        shift 2
        ;;
      -l|--list)
        list_versions
        ;;
      *)
        usage
        ;;
    esac
  done
}

choose_version() {
  if [[ -n ${INTERACTIVE-} ]]; then
    echo "-----"
    echo "Checking the Version..."
    echo "Press ENTER for the latest version, or enter a specific version number (e.g. 1.2.0):"

    read -rp "Version: " -e VERSION

    if [[ $VERSION =~ ^(([0-9]{1,3}[\.]){2}[0-9]{1,3}).* ]]; then
      echo "Installing version $VERSION."
    elif [[ -z $VERSION ]]; then
      VERSION="latest"
      echo "No version specified, installing the latest version."
    else
      echo "Invalid version number. Installing the latest version."
      VERSION="latest"
    fi
  fi

  if [[ -z ${VERSION-} ]]; then
    VERSION="latest"
    echo "No version has been specified, latest will be used."
  fi
}

check_dependencies() {
  local dependencies=("wget" "git" "docker" "docker-compose")
  local not_found=()

  for dep in "${dependencies[@]}"; do
    if ! command -v "$dep" >/dev/null 2>&1; then
      not_found+=("$dep")
    else
      echo "Checking for presence of $dep... found."
    fi
  done

  if [ "${#not_found[@]}" -ne 0 ]; then
    echo "The following dependencies are missing:"
    for missing in "${not_found[@]}"; do
      echo "- $missing"
    done
    echo "Please install the missing dependencies before continuing."
    exit 1
  fi
}

download_and_install() {
  local canasta_url=""

  if [ "$VERSION" == "latest" ]; then
    canasta_url="https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta"
  else
    canasta_url="https://github.com/CanastaWiki/Canasta-CLI/releases/download/v${VERSION}/canasta"
  fi

  echo "Downloading Canasta CLI version $VERSION..."
  wget -q --show-progress "$canasta_url" -O canasta || { echo "Download failed. The version you specified might not exist."; echo "Please use '-l' or '--list' flag to see the available versions or try again."; exit 1; }
  echo "Download was successful; now installing Canasta CLI."
  chmod u=rwx,g=xr,o=x canasta
  sudo mv canasta /usr/local/bin/canasta
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
  echo "Installing was successful."
}

main() {
  parse_arguments "$@"
  choose_version
  check_dependencies
  download_and_install
  echo "Please make sure you have a working kubectl if you wish to use Kubernetes as an orchestrator."
  echo -e "\nUsage: sudo canasta [COMMAND] [ARGUMENTS...]"
}
main "$@"