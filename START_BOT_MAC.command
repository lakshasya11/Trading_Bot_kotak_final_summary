#!/bin/bash

# Get the directory where this script is located
cd "$(dirname "$0")"

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
    echo -e "${RED}❌ Setup not completed! Please run SETUP_MAC.command first${NC}"
    echo -e "${YELLOW}Press any key to exit...${NC}"
    read -n 1
    exit 1
fi

# Check if configuration files exist
if [ ! -f "backend/access_token_user1.json" ] && [ ! -f "backend/access_token.json" ]; then
    echo -e "${RED}❌ Access token not found! Please configure your Kite credentials${NC}"
    echo -e "${YELLOW}Press any key to exit...${NC}"
    read -n 1
    exit 1
fi

echo -e "${GREEN}🚀 Starting V47.14 Trading Bot...${NC}"
echo
echo -e "${BLUE}Frontend will be available at: http://localhost:5173${NC}"
echo -e "${BLUE}Backend API will be available at: http://localhost:8000${NC}"
echo
echo -e "${YELLOW}To stop the bot, press Ctrl+C or run STOP_BOT_MAC.command${NC}"
echo

# Clean up any existing processes on ports
echo -e "${BLUE}🧹 Cleaning up existing processes...${NC}"
lsof -ti:8000 | xargs kill -9 2>/dev/null
lsof -ti:5173 | xargs kill -9 2>/dev/null
lsof -ti:5174 | xargs kill -9 2>/dev/null
sleep 1

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}🛑 Stopping bot...${NC}"
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    lsof -ti:8000 | xargs kill -9 2>/dev/null
    lsof -ti:5173 | xargs kill -9 2>/dev/null
    echo -e "${GREEN}✅ Bot stopped${NC}"
    exit 0
}

# Set up trap for cleanup
trap cleanup SIGINT SIGTERM EXIT

# Start backend in background
echo -e "${BLUE}🔧 Starting backend server...${NC}"
cd backend
source venv/bin/activate
python main.py > ../backend.log 2>&1 &
BACKEND_PID=$!
cd ..

# Wait a moment for backend to start
echo -e "${YELLOW}Waiting for backend to initialize...${NC}"
sleep 3

# Check if backend started successfully
if ! lsof -i:8000 >/dev/null 2>&1; then
    echo -e "${RED}❌ Backend failed to start! Check backend.log for errors${NC}"
    cat backend.log
    echo -e "${YELLOW}Press any key to exit...${NC}"
    read -n 1
    exit 1
fi
echo -e "${GREEN}✅ Backend started successfully${NC}"

# Start frontend in background
echo -e "${BLUE}🎨 Starting frontend...${NC}"
cd frontend
npm run dev > ../frontend.log 2>&1 &
FRONTEND_PID=$!
cd ..

# Wait a moment for frontend to start
sleep 3

# Check if frontend started successfully
if ! lsof -i:5173 >/dev/null 2>&1; then
    echo -e "${RED}❌ Frontend failed to start! Check frontend.log for errors${NC}"
    cat frontend.log
    cleanup
fi
echo -e "${GREEN}✅ Frontend started successfully${NC}"

echo
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}   🎉 Bot is now running!${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${BLUE}   Frontend: http://localhost:5173${NC}"
echo -e "${BLUE}   Backend:  http://localhost:8000${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo
echo -e "${YELLOW}Keep this window open. Press Ctrl+C to stop.${NC}"
echo

# Wait for processes
wait $FRONTEND_PID
cleanup
