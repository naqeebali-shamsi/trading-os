#!/usr/bin/env bash
# mt5_headless.sh — Start MT5 terminal under Xvfb with FileBridgeEA
# Usage: ./mt5_headless.sh [start|stop|status|restart]
set -euo pipefail

WINEPREFIX="${WINEPREFIX:-$HOME/.mt5}"
DISPLAY="${DISPLAY:-:99}"
MT5_DIR="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"
MT5_EXE="$MT5_DIR/terminal64.exe"
XVFB_NUM="${DISPLAY#:}"
PIDFILE="/tmp/mt5_headless.pid"
EA_NAME="FileBridgeEA"   # Must be compiled as FileBridgeEA.ex5 first

# ── Helpers ───────────────────────────────────────────────────
mt5_running() {
    pgrep -f "terminal64.exe" > /dev/null 2>&1
}

xvfb_running() {
    pgrep -f "Xvfb :$XVFB_NUM" > /dev/null 2>&1
}

xvfb_start() {
    if ! xvfb_running; then
        echo "[+] Starting Xvfb on $DISPLAY"
        nohup Xvfb ":$XVFB_NUM" -screen 0 1024x768x24 -ac +extension GLX +render -noreset \
            > /tmp/xvfb_${XVFB_NUM}.log 2>&1 &
        sleep 2
    else
        echo "[i] Xvfb already running on $DISPLAY"
    fi
}

mt5_start() {
    if mt5_running; then
        echo "[i] MT5 already running (PID $(pgrep -f terminal64.exe | head -1))"
        return 0
    fi

    if [ ! -f "$MT5_EXE" ]; then
        echo "[!] MT5 not found at $MT5_EXE"
        exit 1
    fi

    echo "[+] Starting MT5 terminal..."
    # Start MT5 with the EA already selected via /config:file.ini trick
    # MT5 auto-loads the last profile. We rely on the EA being in the 
    # profile, or the user attaching it via the GUI first time.
    # For unattended startup with EA, use the .ini override trick or
    # attach via remote control.
    nohup xvfb-run -a --server-args="-screen 0 1024x768x24 -ac +render -noreset" \
        wine "$MT5_EXE" > /tmp/mt5_terminal.log 2>&1 &

    sleep 5

    if mt5_running; then
        echo "[+] MT5 started successfully"
        pgrep -f "terminal64.exe" | head -1 > "$PIDFILE"
    else
        echo "[!] MT5 failed to start. Check /tmp/mt5_terminal.log"
        exit 1
    fi
}

mt5_stop() {
    echo "[+] Stopping MT5..."
    pkill -f "terminal64.exe" 2>/dev/null || true
    pkill -f "metatester64.exe" 2>/dev/null || true
    sleep 1
    echo "[+] MT5 stopped"
}

mt5_status() {
    if mt5_running; then
        local pid
        pid=$(pgrep -f "terminal64.exe" | head -1)
        echo "[+] MT5 running (PID $pid)"
        # Check ipc files
        IPC_DIR="$WINEPREFIX/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal"
        # Find first terminal subdir with heartbeat
        if [ -d "$IPC_DIR" ]; then
            for d in "$IPC_DIR"/*; do
                if [ -f "$d/MQL5/Files/trading-os/heartbeat.txt" ]; then
                    echo "[+] EA heartbeat: $(cat "$d/MQL5/Files/trading-os/heartbeat.txt")"
                    return 0
                fi
            done
        fi
        echo "[?] MT5 running but no EA heartbeat detected"
    else
        echo "[-] MT5 not running"
    fi
}

# ── Main ──────────────────────────────────────────────────────
case "${1:-start}" in
    start)
        xvfb_start
        mt5_start
        ;;
    stop)
        mt5_stop
        ;;
    restart)
        mt5_stop
        sleep 2
        xvfb_start
        mt5_start
        ;;
    status)
        mt5_status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
