#!/bin/bash

echo "========================================"
echo "     V47.14 Trading Bot - Starting"
echo "========================================"
echo

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if setup was completed
if [ ! -d "backend/venv" ]; then
    echo -e "${RED}❌ Setup not completed! Please run ./SETUP_LINUX.sh first${NC}"
    exit 1
fi

# Check if configuration files exist
# Prefer user_profiles.json for auto-login, fallback to access_token.json
if [ ! -f "backend/user_profiles.json" ]; then
    if [ ! -f "backend/access_token.json" ]; then
        echo
        echo -e "${RED}❌ No authentication configured!${NC}"
        echo
        echo "Please configure ONE of the following:"
        echo "  1. user_profiles.json (RECOMMENDED - for auto-login)"
        echo "  2. access_token.json (fallback - manual token)"
        echo
        echo "Run ./SETUP_LINUX.sh and follow AUTO_LOGIN_SETUP_GUIDE.md"
        echo
        exit 1
    else
        echo -e "${YELLOW}⚠️  Using access_token.json (manual authentication mode)${NC}"
        echo "   For better experience, configure user_profiles.json for auto-login"
    fi
else
    echo -e "${GREEN}✓ user_profiles.json found - auto-login will be used${NC}"
fi
echo

echo -e "${GREEN}🚀 Starting V47.14 Trading Bot...${NC}"
echo
echo -e "${BLUE}📍 Services will be available at:${NC}"
echo -e "${BLUE}   Frontend: http://localhost:3000${NC}"
echo -e "${BLUE}   Backend:  http://localhost:8000${NC}"
echo -e "${BLUE}   API Docs: http://localhost:8000/docs${NC}"
echo
echo -e "${BLUE}🔐 Authentication Mode:${NC}"
if [ -f "backend/user_profiles.json" ]; then
    echo -e "${GREEN}   ✓ Auto-login enabled (using user_profiles.json)${NC}"
else
    echo -e "${GREEN}   ✓ Manual authentication (using access_token.json)${NC}"
fi
echo
echo -e "${YELLOW}⏹️  To stop the bot, press Ctrl+C${NC}"
echo
echo "Starting services..."
echo

# Function to cleanup on exit
cleanup() {
    echo
    echo -e "${YELLOW}============================================================${NC}"
    echo
    echo -e "${YELLOW}🛑 Stopping bot...${NC}"
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    echo -e "${GREEN}✓ Backend stopped${NC}"
    echo -e "${GREEN}✓ Bot shutdown complete${NC}"
    echo
    exit 0
}

# Set up trap for cleanup
trap cleanup SIGINT SIGTERM

# Start backend in background
echo -e "${BLUE}[1/2] Starting backend server...${NC}"
cd backend
source venv/bin/activate
python main.py &
BACKEND_PID=$!
cd ..

# Wait for backend to start
echo -e "${YELLOW}⏳ Waiting for backend to start...${NC}"
sleep 5
echo -e "${GREEN}✓ Backend should be ready now${NC}"
echo

# Start frontend in background
echo -e "${BLUE}[2/2] Starting frontend server...${NC}"
echo -e "${YELLOW}⏳ This may take 30-60 seconds on first run...${NC}"
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

# Wait for processes
wait $FRONTEND_PID
cleanup