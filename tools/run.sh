#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="python3"
REQ_FILE="tools/requirements.txt"
ORCH="tools/orchestrate.py"

# Repository URLs
UPSTREAM_REPO="https://github.com/konveyor/rulesets.git"
DOWNSTREAM_REPO="https://github.com/Azure/appcat-konveyor-rulesets.git"
UPSTREAM_DIR="rulesets"
DOWNSTREAM_DIR="appcat-konveyor-rulesets"

usage() {
  echo "Usage: tools/run.sh [--reset] [--force-step NAME]"
  echo "- Automatically uses pyenv-virtualenv if available, else .venv, else global python"
  echo "- Automatically clones repositories if they don't exist locally"
}

# Clone repositories if they don't exist
clone_repos() {
  echo "Checking for required repositories..."
  
  if [[ ! -d "$UPSTREAM_DIR" ]]; then
    echo "Cloning upstream repository: $UPSTREAM_REPO"
    git clone "$UPSTREAM_REPO" "$UPSTREAM_DIR"
  else
    echo "Upstream repository already exists: $UPSTREAM_DIR"
  fi
  
  if [[ ! -d "$DOWNSTREAM_DIR" ]]; then
    echo "Cloning downstream repository: $DOWNSTREAM_REPO"
    git clone "$DOWNSTREAM_REPO" "$DOWNSTREAM_DIR"
  else
    echo "Downstream repository already exists: $DOWNSTREAM_DIR"
  fi
  
  echo "Repository check complete."
}

RESET_FLAG=""
FORCE_STEP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reset)
      RESET_FLAG="--reset"
      shift
      ;;
    --force-step)
      FORCE_STEP="$2"
      shift 2
      ;;
    -h|--help)
      usage; exit 0;
      ;;
    *)
      echo "Unknown arg: $1"; usage; exit 1;
      ;;
  esac
done

# Clone repositories if they don't exist
clone_repos

# Try pyenv-virtualenv
activate_pyenv() {
  if command -v pyenv >/dev/null 2>&1; then
    # shellcheck disable=SC1090
    if [[ -z "${PYENV_ROOT:-}" ]]; then
      eval "$(pyenv init -)" >/dev/null
    fi
    if command -v pyenv-virtualenv-init >/dev/null 2>&1; then
      eval "$(pyenv virtualenv-init -)" >/dev/null || true
    fi
    local VENV_NAME="appcat-konveyor-rs"
    local PY_VER="3.11.9"
    pyenv install -s "$PY_VER" || true
    if ! pyenv versions --bare | grep -qx "$VENV_NAME"; then
      pyenv virtualenv -f "$PY_VER" "$VENV_NAME"
    fi
    pyenv local "$VENV_NAME"
    return 0
  fi
  return 1
}

# Try built-in venv
activate_local_venv() {
  if [[ ! -d .venv ]]; then
    "$PYTHON_BIN" -m venv .venv || return 1
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  return 0
}

env_mode="global"
if activate_pyenv; then
  env_mode="pyenv"
elif activate_local_venv; then
  env_mode="venv"
fi

echo "Using environment: $env_mode"

if [[ -f "$REQ_FILE" ]]; then
  pip install -r "$REQ_FILE"
fi

if [[ -n "$RESET_FLAG" ]]; then
  "$PYTHON_BIN" "$ORCH" --reset
fi

if [[ -n "$FORCE_STEP" ]]; then
  "$PYTHON_BIN" "$ORCH" --force-step "$FORCE_STEP"
else
  "$PYTHON_BIN" "$ORCH" --run
fi


