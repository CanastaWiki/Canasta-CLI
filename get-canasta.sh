#!/usr/bin/env bash
# get-canasta.sh — Install Canasta-Ansible from scratch.
#
# Usage:
#   curl -fsSL https://get.canasta.wiki | bash
#   curl -fsSL https://get.canasta.wiki | bash -s -- --native
#   curl -fsSL https://get.canasta.wiki | bash -s -- --docker
#
# Documentation: https://canasta.wiki/wiki/Help:Installation
#
# Flags:
#   --native    Install canasta-native (requires Python 3.10+, git)
#   --docker    Install canasta-docker (requires Docker only, default)
#   --prefix    Installation prefix (default: /opt/canasta-ansible for native)
#
# Linux native installs create a 'canasta' system group. Add users with:
#   sudo usermod -aG canasta $USER
#
# macOS native installs use a user-owned path (no group needed).

set -euo pipefail

REPO_URL="https://github.com/CanastaWiki/Canasta-Ansible.git"
DOCKER_WRAPPER_URL="https://raw.githubusercontent.com/CanastaWiki/Canasta-Ansible/main/canasta-docker"
BIN_DIR="/usr/local/bin"
MODE=""
PREFIX=""

# --- Helpers -----------------------------------------------------------------

info()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m%s\033[0m\n' "$*" >&2; }
error() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        error "Required command not found: $1"
    fi
}

install_native_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        info "Installing system dependencies (git, python3, python3-venv)..."
        $SUDO apt-get update -qq
        $SUDO apt-get install -y -qq git python3 python3-venv >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        info "Installing system dependencies (git, python3)..."
        $SUDO dnf install -y -q git python3
    elif command -v yum >/dev/null 2>&1; then
        info "Installing system dependencies (git, python3)..."
        $SUDO yum install -y -q git python3
    fi
}

check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        error "python3 not found. Install it and re-run."
    fi
    local pyver
    pyver="$(python3 -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)" || \
        error "Could not determine Python version"
    if [[ "$pyver" -lt 10 ]]; then
        error "Python 3.10+ required (found 3.${pyver})"
    fi
    local test_venv
    test_venv="$(mktemp -d)"
    if ! python3 -m venv "$test_venv" 2>/dev/null; then
        rm -rf "$test_venv"
        if [[ "$(uname -s)" == "Darwin" ]]; then
            error "python3 -m venv failed. Try: brew install python3"
        else
            error "python3 -m venv failed. On Debian/Ubuntu: sudo apt install python3-venv"
        fi
    fi
    rm -rf "$test_venv"
}

check_docker() {
    need_cmd docker
    if ! docker info >/dev/null 2>&1; then
        warn "Docker is installed but the daemon is not running."
        warn "Start it with: sudo systemctl start docker"
        warn "Continuing with install — you'll need Docker running to use Canasta."
    fi
}

need_sudo() {
    if [[ "$(id -u)" != "0" ]]; then
        if command -v sudo >/dev/null 2>&1; then
            SUDO="sudo"
        else
            error "This operation requires root. Run with sudo or as root."
        fi
    else
        SUDO=""
    fi
}

# --- Platform detection ------------------------------------------------------

detect_platform() {
    OS="$(uname -s)"
    ARCH="$(uname -m)"

    IS_WSL=false
    if [[ -f /proc/version ]] && grep -qi microsoft /proc/version 2>/dev/null; then
        IS_WSL=true
    fi

    case "$OS" in
        Linux)  PLATFORM="linux" ;;
        Darwin) PLATFORM="macos" ;;
        *)      error "Unsupported platform: $OS" ;;
    esac
}

# --- Parse arguments ---------------------------------------------------------

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --native) MODE="native" ;;
            --docker) MODE="docker" ;;
            --prefix) PREFIX="$2"; shift ;;
            --prefix=*) PREFIX="${1#*=}" ;;
            -h|--help)
                echo "Usage: get-canasta.sh [--native|--docker] [--prefix PATH]"
                exit 0
                ;;
            *) error "Unknown option: $1" ;;
        esac
        shift
    done

    if [[ -z "$MODE" ]]; then
        MODE="docker"
    fi
}

# --- Docker mode install -----------------------------------------------------

install_docker_mode() {
    info "Installing Canasta (Docker mode)..."

    check_docker
    need_cmd curl
    need_sudo

    info "Downloading canasta-docker wrapper..."
    $SUDO curl -fsSL --retry 3 --retry-delay 5 \
        -o "${BIN_DIR}/canasta-docker" "$DOCKER_WRAPPER_URL"
    $SUDO chmod +x "${BIN_DIR}/canasta-docker"

    info "Creating canasta symlink..."
    $SUDO ln -sf "${BIN_DIR}/canasta-docker" "${BIN_DIR}/canasta"

    info ""
    info "Canasta installed (Docker mode)."
    info "  canasta-docker: ${BIN_DIR}/canasta-docker"
    info "  canasta:        ${BIN_DIR}/canasta -> canasta-docker"
    info ""
    info "Verify: canasta version"
}

# --- Native mode install (Linux) --------------------------------------------

install_native_linux() {
    local install_dir="${PREFIX:-/opt/canasta-ansible}"

    need_sudo
    install_native_deps

    need_cmd git
    check_python

    info "Installing Canasta (native mode, Linux)..."

    if [[ "$IS_WSL" == true ]]; then
        warn "Detected WSL. Native install will work in WSL2."
        warn "If using WSL1, Docker integration may have issues."
    fi

    # Create canasta group if it doesn't exist
    if ! getent group canasta >/dev/null 2>&1; then
        info "Creating 'canasta' system group..."
        $SUDO groupadd --system canasta
    fi

    # Clone or update the repo
    if [[ -d "${install_dir}/.git" ]]; then
        info "Updating existing installation at ${install_dir}..."
        $SUDO git -C "$install_dir" pull --ff-only
    else
        info "Cloning Canasta-Ansible to ${install_dir}..."
        $SUDO git clone "$REPO_URL" "$install_dir"
    fi

    # Set group ownership
    info "Setting group ownership to 'canasta'..."
    $SUDO chgrp -R canasta "$install_dir"
    $SUDO chmod -R g+w "$install_dir"

    # Mark as safe directory for git
    $SUDO git config --system --add safe.directory "$install_dir" 2>/dev/null || true

    # Create venv and install deps
    info "Creating Python virtual environment..."
    $SUDO python3 -m venv "${install_dir}/.venv"
    $SUDO "${install_dir}/.venv/bin/pip" install --quiet -r "${install_dir}/requirements.txt"

    # Set group ownership on venv too
    $SUDO chgrp -R canasta "${install_dir}/.venv"
    $SUDO chmod -R g+w "${install_dir}/.venv"

    # Build metadata
    info "Writing build metadata..."
    $SUDO bash -c "cd '${install_dir}' && git rev-parse --short HEAD > BUILD_COMMIT && git log -1 --format=%cd --date=format:'%Y-%m-%d %H:%M:%S' > BUILD_DATE"

    # Install Ansible collections
    if [[ -f "${install_dir}/requirements.yml" ]]; then
        info "Installing Ansible collections..."
        $SUDO "${install_dir}/.venv/bin/ansible-galaxy" collection install \
            -r "${install_dir}/requirements.yml" \
            -p /usr/share/ansible/collections 2>/dev/null || true
    fi

    # Create symlinks
    info "Creating symlinks in ${BIN_DIR}..."
    $SUDO ln -sf "${install_dir}/canasta-native" "${BIN_DIR}/canasta-native"
    $SUDO ln -sf "${install_dir}/canasta-docker" "${BIN_DIR}/canasta-docker"
    $SUDO ln -sf "${BIN_DIR}/canasta-native" "${BIN_DIR}/canasta"

    # Install Docker if missing
    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
        canasta install docker
    fi
}

# --- Native mode install (macOS) --------------------------------------------

install_native_macos() {
    local install_dir="${PREFIX:-${HOME}/canasta-ansible}"

    need_cmd git
    check_python

    info "Installing Canasta (native mode, macOS)..."

    # Clone or update the repo (user-owned, no sudo needed for the repo)
    if [[ -d "${install_dir}/.git" ]]; then
        info "Updating existing installation at ${install_dir}..."
        git -C "$install_dir" pull --ff-only
    else
        info "Cloning Canasta-Ansible to ${install_dir}..."
        git clone "$REPO_URL" "$install_dir"
    fi

    # Create venv and install deps
    info "Creating Python virtual environment..."
    python3 -m venv "${install_dir}/.venv"
    "${install_dir}/.venv/bin/pip" install --quiet -r "${install_dir}/requirements.txt"

    # Build metadata
    info "Writing build metadata..."
    (cd "$install_dir" && git rev-parse --short HEAD > BUILD_COMMIT && git log -1 --format=%cd --date=format:'%Y-%m-%d %H:%M:%S' > BUILD_DATE)

    # Install Ansible collections
    if [[ -f "${install_dir}/requirements.yml" ]]; then
        info "Installing Ansible collections..."
        "${install_dir}/.venv/bin/ansible-galaxy" collection install \
            -r "${install_dir}/requirements.yml" 2>/dev/null || true
    fi

    # Symlinks need sudo for /usr/local/bin
    need_sudo
    info "Creating symlinks in ${BIN_DIR}..."
    $SUDO ln -sf "${install_dir}/canasta-native" "${BIN_DIR}/canasta-native"
    $SUDO ln -sf "${install_dir}/canasta-docker" "${BIN_DIR}/canasta-docker"
    $SUDO ln -sf "${BIN_DIR}/canasta-native" "${BIN_DIR}/canasta"
}

# --- Post-install check ------------------------------------------------------

post_install_summary() {
    local install_dir="$1"
    local platform="$2"

    info ""
    info "Running canasta doctor..."
    canasta doctor 2>/dev/null || true

    info ""
    info "========================================"
    info "Canasta installed (native mode)."
    info "  Install dir:    ${install_dir}"
    info "  canasta-native: ${BIN_DIR}/canasta-native"
    info "  canasta-docker: ${BIN_DIR}/canasta-docker"
    info "  canasta:        ${BIN_DIR}/canasta -> canasta-native"

    if ! command -v git-crypt >/dev/null 2>&1; then
        info ""
        info "Optional: install git-crypt (needed for gitops):"
        info "  canasta install git-crypt"
    fi

    if [[ "$platform" == "linux" ]]; then
        info ""
        info "Add yourself to the required groups:"
        info "  sudo usermod -aG canasta,docker,www-data \$USER"
        info "Then log out and back in (or: newgrp canasta)"
    fi

    info "========================================"
}

# --- Main --------------------------------------------------------------------

main() {
    parse_args "$@"
    detect_platform

    case "$MODE" in
        docker)
            install_docker_mode
            ;;
        native)
            case "$PLATFORM" in
                linux)
                    install_native_linux
                    post_install_summary "/opt/canasta-ansible" "linux"
                    ;;
                macos)
                    install_native_macos
                    post_install_summary "${PREFIX:-${HOME}/canasta-ansible}" "macos"
                    ;;
            esac
            ;;
    esac
}

main "$@"
