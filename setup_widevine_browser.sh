#!/usr/bin/env bash
set -euo pipefail

# setup_widevine_browser.sh
# Prepares the Python browser project, locates a local Widevine CDM,
# optionally installs Google Chrome on supported Debian/Ubuntu x86_64 systems,
# and launches browser.py with DRM enabled when possible.
#
# Usage examples:
#   ./setup_widevine_browser.sh
#   ./setup_widevine_browser.sh youtube.com
#   ./setup_widevine_browser.sh --widevine-path /path/to/libwidevinecdm.so netflix.com
#   ./setup_widevine_browser.sh --install-chrome netflix.com
#   ./setup_widevine_browser.sh --no-launch
#
# Notes:
# - This script does NOT bundle Widevine.
# - It relies on a locally installed Widevine CDM, typically from Chrome.
# - browser.py already supports --widevine-path and QTWEBENGINE_CHROMIUM_FLAGS.

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BROWSER_PY="$PROJECT_DIR/browser.py"
REQ_FILE="$PROJECT_DIR/requirements.txt"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="python3"
INSTALL_CHROME=0
NO_LAUNCH=0
EXPLICIT_WIDEVINE=""
STARTUP_URL=""
PASS_THROUGH=()

log() {
  printf '[setup] %s\n' "$*"
}

warn() {
  printf '[setup] warning: %s\n' "$*" >&2
}

die() {
  printf '[setup] error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<USAGE
Usage:
  $(basename "$0") [options] [startup_url] [-- extra browser.py args]

Options:
  --install-chrome          Install Google Chrome stable on Ubuntu/Debian x86_64.
  --widevine-path PATH      Use this exact Widevine CDM library.
  --python PATH             Python interpreter to use. Default: python3
  --no-launch               Prepare everything, print status, but do not start the browser.
  --help                    Show this help.

Examples:
  $(basename "$0") youtube.com
  $(basename "$0") --install-chrome netflix.com
  $(basename "$0") --widevine-path /opt/google/chrome/libwidevinecdm.so
USAGE
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

is_debian_like() {
  [[ -f /etc/debian_version ]]
}

arch_name() {
  uname -m
}

find_widevine() {
  local candidates=()
  local home_dir
  home_dir="${HOME:-}"

  if [[ -n "$EXPLICIT_WIDEVINE" ]]; then
    candidates+=("$EXPLICIT_WIDEVINE")
  fi

  if [[ -n "${PYMEDIABROWSER_WIDEVINE_PATH:-}" ]]; then
    candidates+=("$PYMEDIABROWSER_WIDEVINE_PATH")
  fi

  candidates+=(
    "/opt/google/chrome/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so"
    "/opt/google/chrome/libwidevinecdm.so"
    "/usr/lib/chromium/libwidevinecdm.so"
    "/usr/lib64/chromium/libwidevinecdm.so"
    "/usr/lib/chromium-browser/libwidevinecdm.so"
    "/usr/lib/chromium/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so"
    "/usr/lib/chromium-browser/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so"
    "/snap/chromium/current/usr/lib/chromium-browser/libwidevinecdm.so"
  )

  if [[ -n "$home_dir" ]]; then
    local base
    for base in \
      "$home_dir/.config/google-chrome/WidevineCdm" \
      "$home_dir/.config/chromium/WidevineCdm"
    do
      if [[ -d "$base" ]]; then
        while IFS= read -r file; do
          candidates+=("$file")
        done < <(find "$base" -type f -path '*/_platform_specific/linux_x64/libwidevinecdm.so' 2>/dev/null | sort -r)
      fi
    done
  fi

  local c
  for c in "${candidates[@]}"; do
    if [[ -f "$c" ]]; then
      printf '%s\n' "$c"
      return 0
    fi
  done

  return 1
}

install_chrome_if_requested() {
  if [[ "$INSTALL_CHROME" -eq 0 ]]; then
    return 0
  fi

  need_cmd sudo
  need_cmd wget
  need_cmd apt-get

  if ! is_debian_like; then
    die "--install-chrome is currently implemented for Debian/Ubuntu-style systems only"
  fi

  case "$(arch_name)" in
    x86_64|amd64)
      ;;
    *)
      die "Google Chrome Linux packages are generally provided for 64-bit x86 systems, not $(arch_name)"
      ;;
  esac

  if command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1; then
    log "Google Chrome already appears to be installed"
    return 0
  fi

  local deb_file
  deb_file="/tmp/google-chrome-stable_current_amd64.deb"

  log "Downloading Google Chrome stable package"
  wget -O "$deb_file" "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"

  log "Installing Google Chrome stable package"
  sudo apt-get update
  sudo apt-get install -y "$deb_file"
}

prepare_python_env() {
  need_cmd "$PYTHON_BIN"

  if [[ ! -f "$BROWSER_PY" ]]; then
    die "browser.py was not found in $PROJECT_DIR"
  fi
  if [[ ! -f "$REQ_FILE" ]]; then
    die "requirements.txt was not found in $PROJECT_DIR"
  fi

  log "Creating virtual environment in $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  log "Upgrading pip and wheel"
  python -m pip install --upgrade pip wheel

  log "Installing Python requirements"
  python -m pip install -r "$REQ_FILE"
}

launch_browser() {
  local widevine_path=""
  local qt_flags="${QTWEBENGINE_CHROMIUM_FLAGS:-}"

  if widevine_path="$(find_widevine)"; then
    export PYMEDIABROWSER_WIDEVINE_PATH="$widevine_path"

    if [[ "$qt_flags" == *"--widevine-path="* ]]; then
      log "Using existing QTWEBENGINE_CHROMIUM_FLAGS widevine-path"
    else
      export QTWEBENGINE_CHROMIUM_FLAGS="${qt_flags:+$qt_flags }--widevine-path=\"$widevine_path\""
      log "Configured Widevine CDM: $widevine_path"
    fi
  else
    warn "No local Widevine CDM was found. The browser can still start, but DRM playback may fail."
    warn "Try --install-chrome, or pass --widevine-path /path/to/libwidevinecdm.so"
  fi

  if [[ "$NO_LAUNCH" -eq 1 ]]; then
    log "Preparation complete. Browser not launched because --no-launch was used."
    if [[ -n "${PYMEDIABROWSER_WIDEVINE_PATH:-}" ]]; then
      log "Detected Widevine: ${PYMEDIABROWSER_WIDEVINE_PATH}"
    fi
    return 0
  fi

  local cmd=(python "$BROWSER_PY")
  if [[ -n "$STARTUP_URL" ]]; then
    cmd+=("$STARTUP_URL")
  fi
  if [[ ${#PASS_THROUGH[@]} -gt 0 ]]; then
    cmd+=("${PASS_THROUGH[@]}")
  fi

  log "Launching browser.py"
  exec "${cmd[@]}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --install-chrome)
        INSTALL_CHROME=1
        shift
        ;;
      --widevine-path)
        [[ $# -ge 2 ]] || die "Missing value after --widevine-path"
        EXPLICIT_WIDEVINE="$2"
        shift 2
        ;;
      --widevine-path=*)
        EXPLICIT_WIDEVINE="${1#*=}"
        shift
        ;;
      --python)
        [[ $# -ge 2 ]] || die "Missing value after --python"
        PYTHON_BIN="$2"
        shift 2
        ;;
      --no-launch)
        NO_LAUNCH=1
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      --)
        shift
        PASS_THROUGH+=("$@")
        break
        ;;
      *)
        if [[ -z "$STARTUP_URL" ]]; then
          STARTUP_URL="$1"
        else
          PASS_THROUGH+=("$1")
        fi
        shift
        ;;
    esac
  done
}

main() {
  parse_args "$@"
  install_chrome_if_requested
  prepare_python_env
  launch_browser
}

main "$@"
