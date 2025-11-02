#!/bin/bash
# Stop Quiz Generation MCP Server
# Usage: ./stop_server.sh

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PID_FILE="$SCRIPT_DIR/server.pid"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Quiz Generation MCP Server Stopper${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if PID file exists
if [ ! -f "$PID_FILE" ]; then
    echo -e "${YELLOW}⚠️  No PID file found. Checking for running processes...${NC}"
    
    # Try to find running server by process name
    SERVER_PIDS=$(ps aux | grep quiz_generate_server.py | grep -v grep | awk '{print $2}')
    
    if [ -z "$SERVER_PIDS" ]; then
        echo -e "${GREEN}✅ No server processes found running${NC}"
        exit 0
    else
        echo -e "${YELLOW}Found server process(es): $SERVER_PIDS${NC}"
        echo -e "${BLUE}Stopping them...${NC}"
        echo "$SERVER_PIDS" | xargs kill 2>/dev/null
        sleep 1
        
        # Check if still running
        REMAINING=$(ps aux | grep quiz_generate_server.py | grep -v grep | awk '{print $2}')
        if [ -z "$REMAINING" ]; then
            echo -e "${GREEN}✅ Server stopped successfully${NC}"
            exit 0
        else
            echo -e "${RED}⚠️  Some processes still running. Force killing...${NC}"
            echo "$REMAINING" | xargs kill -9 2>/dev/null
            echo -e "${GREEN}✅ Server force stopped${NC}"
            exit 0
        fi
    fi
fi

# Read PID from file
SERVER_PID=$(cat "$PID_FILE")

echo "Stopping server (PID: $SERVER_PID)..."

# Check if process exists
if ! ps -p $SERVER_PID > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Process $SERVER_PID is not running${NC}"
    rm -f "$PID_FILE"
    
    # Double-check for any other instances
    SERVER_PIDS=$(ps aux | grep quiz_generate_server.py | grep -v grep | awk '{print $2}')
    if [ ! -z "$SERVER_PIDS" ]; then
        echo -e "${YELLOW}Found other server process(es): $SERVER_PIDS${NC}"
        echo "$SERVER_PIDS" | xargs kill 2>/dev/null
        sleep 1
    fi
    
    echo -e "${GREEN}✅ Cleanup complete${NC}"
    exit 0
fi

# Try graceful shutdown first
echo "Sending TERM signal..."
kill $SERVER_PID 2>/dev/null

# Wait up to 5 seconds for graceful shutdown
for i in {1..5}; do
    if ! ps -p $SERVER_PID > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Server stopped gracefully${NC}"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
    echo -n "."
done
echo ""

# If still running, force kill
if ps -p $SERVER_PID > /dev/null 2>&1; then
    echo -e "${RED}Server didn't stop gracefully. Force killing...${NC}"
    kill -9 $SERVER_PID 2>/dev/null
    sleep 1
    
    if ps -p $SERVER_PID > /dev/null 2>&1; then
        echo -e "${RED}❌ Failed to stop server${NC}"
        exit 1
    else
        echo -e "${GREEN}✅ Server force stopped${NC}"
        rm -f "$PID_FILE"
        exit 0
    fi
fi

rm -f "$PID_FILE"
echo -e "${GREEN}✅ Server stopped successfully${NC}"

