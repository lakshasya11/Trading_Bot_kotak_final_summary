#!/bin/bash

# Get the directory where this script is located
cd "$(dirname "$0")"

echo "========================================"
echo "     V47.14 Trading Bot - Stopping"
echo "========================================"
echo

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}🛑 Stopping all bot processes...${NC}"

# Kill processes on port 8000 (backend)
if lsof -ti:8000 >/dev/null 2>&1; then
    lsof -ti:8000 | xargs kill -9 2>/dev/null
    echo -e "${GREEN}✅ Backend stopped (port 8000)${NC}"
else
    echo -e "${BLUE}ℹ️ No backend processes found on port 8000${NC}"
fi

# Kill processes on port 5173 (frontend)
if lsof -ti:5173 >/dev/null 2>&1; then
    lsof -ti:5173 | xargs kill -9 2>/dev/null
    echo -e "${GREEN}✅ Frontend stopped (port 5173)${NC}"
else
    echo -e "${BLUE}ℹ️ No frontend processes found on port 5173${NC}"
fi

# Kill processes on port 5174 (alternate frontend)
if lsof -ti:5174 >/dev/null 2>&1; then
    lsof -ti:5174 | xargs kill -9 2>/dev/null
    echo -e "${GREEN}✅ Frontend stopped (port 5174)${NC}"
fi

# Kill any stray Python main.py processes
pkill -f "python main.py" 2>/dev/null
pkill -f "npm run dev" 2>/dev/null

echo
echo -e "${GREEN}✅ V47.14 Trading Bot stopped successfully${NC}"
echo
echo -e "${YELLOW}Press any key to close...${NC}"
read -n 1
