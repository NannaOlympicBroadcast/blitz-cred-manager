#!/usr/bin/env sh
# Blitz Credential Manager CLI Installer (Linux / macOS)
set -e

SERVER_URL="${CRED_SERVER_URL:-https://cred-manager.blitz48.blitz.cloud}"
GITHUB_RAW="https://raw.githubusercontent.com/NannaOlympicBroadcast/blitz-cred-manager/main/cli"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) ARCH="unknown" ;;
esac

if [ -w "/usr/local/bin" ]; then
  INSTALL_DIR="/usr/local/bin"
else
  INSTALL_DIR="$HOME/.local/bin"
  mkdir -p "$INSTALL_DIR"
fi

TARGET="$INSTALL_DIR/cred-cli"
BIN_NAME="cred-cli-${OS}-${ARCH}"

echo "[cred-cli] Installing Blitz Credential Manager CLI to $TARGET ..."

DOWNLOADED=0
if [ -n "$SERVER_URL" ] && [ "${SERVER_URL#__}" = "$SERVER_URL" ]; then
  if curl -fsSL "$SERVER_URL/cli/$BIN_NAME" -o "$TARGET" 2>/dev/null; then
    chmod +x "$TARGET"
    DOWNLOADED=1
  elif curl -fsSL "$SERVER_URL/cli/cred-cli.py" -o "$TARGET" 2>/dev/null; then
    chmod +x "$TARGET"
    DOWNLOADED=1
  fi
fi

if [ "$DOWNLOADED" = "0" ]; then
  if curl -fsSL "$GITHUB_RAW/bin/$BIN_NAME" -o "$TARGET" 2>/dev/null; then
    chmod +x "$TARGET"
    DOWNLOADED=1
  elif curl -fsSL "$GITHUB_RAW/cred-cli.py" -o "$TARGET" 2>/dev/null; then
    chmod +x "$TARGET"
    DOWNLOADED=1
  fi
fi

if [ "$DOWNLOADED" = "0" ]; then
  echo "[cred-cli] Error: failed to download cred-cli" >&2
  exit 1
fi

echo "[cred-cli] Installed successfully -> $TARGET"
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *) echo "[cred-cli] Note: Add $INSTALL_DIR to your PATH: export PATH=\"$INSTALL_DIR:\$PATH\"" ;;
esac
"$TARGET" version || true
