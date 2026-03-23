#!/bin/bash

echo "========================================"
echo "     V47.14 Trading Bot - Stopping"
echo "========================================"
echo

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}🛑 Stopping all bot processes...${NC}"

# Kill Python processes (backend)
pkill -f "python main.py" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Backend stopped${NC}"
else
    echo -e "${BLUE}ℹ️ No backend processes found${NC}"
fi

# Kill Node processes (frontend)
pkill -f "npm run dev" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Frontend stopped${NC}"
else
    echo -e "${BLUE}ℹ️ No frontend processes found${NC}"
fi

echo
echo -e "${GREEN}✅ V47.14 Trading Bot stopped successfully${NC}"
echo