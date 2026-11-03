#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
xattr -cr "$DIR" 2>/dev/null || true
bash "$DIR/安装与体检.command" "$@"
