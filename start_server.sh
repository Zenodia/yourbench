#!/bin/bash
# Start Quiz Generation MCP Server
# Usage: 
#   ./start_server.sh              # Start with default settings
#   ./start_server.sh 1            # Start with MAX_WORKERS=1
#   ./start_server.sh 8            # Start with MAX_WORKERS=8

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_BIN="python"
SERVER_SCRIPT="$SCRIPT_DIR/quiz_generate_server.py"
LOG_FILE="$SCRIPT_DIR/server.log"
PID_FILE="$SCRIPT_DIR/server.pid"

# Get MAX_WORKERS from argument or use default
MAX_WORKERS=${1:-8}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Quiz Generation MCP Server Starter${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if server is already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p $OLD_PID > /dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  Server is already running (PID: $OLD_PID)${NC}"
        echo -e "${YELLOW}   Stop it first with: ./stop_server.sh${NC}"
        exit 1
    else
        # Stale PID file
        rm -f "$PID_FILE"
    fi
fi

# Check if server script exists
if [ ! -f "$SERVER_SCRIPT" ]; then
    echo -e "${RED}❌ Server script not found: $SERVER_SCRIPT${NC}"
    exit 1
fi

echo -e "${GREEN}🔧 Configuration:${NC}"
echo "   MAX_WORKERS: $MAX_WORKERS"
echo "   Python: $PYTHON_BIN"
echo "   Log file: $LOG_FILE"
echo ""

# Start the server
echo -e "${GREEN}🚀 Starting server...${NC}"
cd "$SCRIPT_DIR"

# Export environment variable and start
QUIZ_SERVER_MAX_WORKERS=$MAX_WORKERS $PYTHON_BIN "$SERVER_SCRIPT" > "$LOG_FILE" 2>&1 &
SERVER_PID=$!

# Save PID
echo $SERVER_PID > "$PID_FILE"

# Wait a bit for startup
sleep 3

# Check if process is still running
if ps -p $SERVER_PID > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Server started successfully!${NC}"
    echo "   PID: $SERVER_PID"
    echo "   URL: http://127.0.0.1:4777/mcp"
    echo ""
    echo -e "${BLUE}📊 Server Configuration:${NC}"
    tail -20 "$LOG_FILE" | grep -E "(MAX_WORKERS|Worker|Configuration)" | head -5
    echo ""
    echo -e "${BLUE}Commands:${NC}"
    echo "   View logs:  tail -f $LOG_FILE"
    echo "   Stop server: ./stop_server.sh"
    echo "   Test server: ./client.sh"
else
    echo -e "${RED}❌ Server failed to start!${NC}"
    echo "Check logs: $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi

