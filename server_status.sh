#!/bin/bash
# Check Quiz Generation MCP Server Status
# Usage: ./server_status.sh

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PID_FILE="$SCRIPT_DIR/server.pid"
LOG_FILE="$SCRIPT_DIR/server.log"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Quiz Generation MCP Server Status${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check PID file
if [ -f "$PID_FILE" ]; then
    SERVER_PID=$(cat "$PID_FILE")
    echo "PID file: $PID_FILE"
    echo "PID: $SERVER_PID"
    echo ""
    
    if ps -p $SERVER_PID > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Server is RUNNING${NC}"
        echo ""
        
        # Show process details
        echo -e "${BLUE}Process Details:${NC}"
        ps -p $SERVER_PID -o pid,ppid,user,%cpu,%mem,etime,command | tail -1
        echo ""
        
        # Check port
        echo -e "${BLUE}Port Status:${NC}"
        lsof -i :4777 2>/dev/null | grep LISTEN || echo "  Port 4777: Not listening"
        echo ""
        
        # Show configuration from logs
        if [ -f "$LOG_FILE" ]; then
            echo -e "${BLUE}Server Configuration:${NC}"
            grep -E "MAX_WORKERS_LIMIT:|MEMORY_PER_WORKER|BATCH_SIZE|AUTO_TUNE" "$LOG_FILE" | tail -5
            echo ""
            
            echo -e "${BLUE}Recent Activity (last 10 lines):${NC}"
            tail -10 "$LOG_FILE"
        fi
    else
        echo -e "${RED}❌ Server is NOT RUNNING (stale PID file)${NC}"
        echo "PID $SERVER_PID is not active"
    fi
else
    echo "PID file: Not found"
    echo ""
    
    # Check for running processes
    SERVER_PIDS=$(ps aux | grep quiz_generate_server.py | grep -v grep | awk '{print $2}')
    
    if [ -z "$SERVER_PIDS" ]; then
        echo -e "${RED}❌ Server is NOT RUNNING${NC}"
    else
        echo -e "${YELLOW}⚠️  Server process found but no PID file!${NC}"
        echo "Running PIDs: $SERVER_PIDS"
        echo ""
        echo -e "${BLUE}Process Details:${NC}"
        for pid in $SERVER_PIDS; do
            ps -p $pid -o pid,ppid,user,%cpu,%mem,etime,command
        done
    fi
fi

echo ""
echo -e "${BLUE}Available Commands:${NC}"
echo "  Start:  ./start_server.sh [workers]"
echo "  Stop:   ./stop_server.sh"
echo "  Logs:   tail -f $LOG_FILE"
echo "  Test:   ./client.sh"

