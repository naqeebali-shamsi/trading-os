#!/bin/bash
# /mnt/e/NomadCrew[GROWTH]/trading-os/v2/scripts/start_supervisor.sh
# Start Autonome Trading Supervisor with TimesFM support via Python 3.11

set -euo pipefail

cd /mnt/e/NomadCrew[GROWTH]/trading-os/v2

# Python 3.11 venv with torch+TimesFM
PYTHON_311="/mnt/e/NomadCrew[GROWTH]/trading-os/timesfm_env/bin/python"
TIMESFM_PACKAGES="/mnt/e/NomadCrew[GROWTH]/trading-os/timesfm_env/lib/python3.11/site-packages"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${PWD}:${TIMESFM_PACKAGES}"

# Verify TimesFM loads
$PYTHON_311 -c "import timesfm; print('TimesFM version:', getattr(timesfm, '__version__', '2.0')); print('TimesFM OK')" 2>&1

# Start supervisor
exec $PYTHON_311 -u -m autonome.supervisor.main
