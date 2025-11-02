#!/bin/bash
# Monitor CPU usage for quiz generation server

SERVER_PID=$(pgrep -f quiz_generate_server)

if [ -z "$SERVER_PID" ]; then
    echo "❌ Server not running"
    exit 1
fi

echo "Monitoring CPU for quiz_generate_server (PID: $SERVER_PID)"
echo "Press Ctrl+C to stop"
echo ""
echo "Time     | CPU%  | Memory(MB) | Status"
echo "---------|-------|------------|------------------"

while true; do
    # macOS compatible version
    STATS=$(ps -p $SERVER_PID -o %cpu=,%mem=,rss= 2>/dev/null)

    if [ -z "$STATS" ]; then
        echo "$(date +%H:%M:%S) | Server stopped"
        exit 0
    fi

    CPU=$(echo $STATS | awk '{print $1}')
    MEM_MB=$(echo $STATS | awk '{printf "%.0f", $3/1024}')

    # Get thread count (macOS way)
    THREADS=$(ps -M -p $SERVER_PID 2>/dev/null | wc -l)
    THREADS=$((THREADS - 1))  # Subtract header line
    # Status indicator
    if (( $(echo "$CPU > 50" | bc -l) )); then
        STATUS="🔥 High CPU"
    elif (( $(echo "$CPU > 10" | bc -l) )); then
        STATUS="⚙️  Active"
    else
        STATUS="💤 Idle"
    fi

    printf "%s | %5.1f%% | %10s | %s (threads: %d)\n" \
        "$(date +%H:%M:%S)" "$CPU" "$MEM_MB" "$STATUS" "$THREADS"

    sleep 2
done
