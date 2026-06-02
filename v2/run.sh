#!/bin/bash
# v2/run.sh -- convenience launcher
set -euo pipefail

cd "$(dirname "$0")"

# Ensure virtual env
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

# Validate secrets exist
if [ ! -f config/secrets.yaml ]; then
    echo "ERROR: config/secrets.yaml missing"
    exit 1
fi

# Validate mode
MODE=$(python3 -c "import yaml; print(yaml.safe_load(open('config/settings.yaml'))['system']['mode'])")
echo "MODE=$MODE"
if [ "$MODE" = "LIVE" ]; then
    read -p "LIVE MODE confirmed? (type YES) " confirm
    if [ "$confirm" != "YES" ]; then
        echo "Aborted"
        exit 1
    fi
fi

# Create data dir
mkdir -p data

# Run
exec python3 -m autonome.supervisor.main
