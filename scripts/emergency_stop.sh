#!/bin/bash
# scripts/emergency_stop.sh — Circuit breaker halt (delegates to portable Python)
# Usage: bash scripts/emergency_stop.sh [reason]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REASON="${1:-manual_emergency}"
PYTHON="${TRADING_OS_PYTHON:-python3}"

exec "$PYTHON" "$ROOT/scripts/emergency_stop.py" "$REASON"
