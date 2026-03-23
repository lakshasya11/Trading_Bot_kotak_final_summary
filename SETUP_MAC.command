#!/bin/bash

# Get the directory where this script is located
cd "$(dirname "$0")"

echo "========================================"
echo "   V47.14 Trading Bot - Mac Setup"
echo "========================================"
echo

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    echo -e "${RED}❌ Homebrew not found!${NC}"
    echo -e "${YELLOW}Installing Homebrew...${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 not found!${NC}"
    echo -e "${YELLOW}Installing Python 3 via Homebrew...${NC}"
    brew install python@3.13
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo -e "${RED}❌ Node.js not found!${NC}"
    echo -e "${YELLOW}Installing Node.js via Homebrew...${NC}"
    brew install node
fi

echo -e "${GREEN}✅ Prerequisites installed${NC}"
echo

# Backend Setup
echo -e "${BLUE}🔧 Setting up backend...${NC}"
cd backend

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating Python virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source venv/bin/activate

# Install Python dependencies
echo -e "${YELLOW}Installing Python packages...${NC}"
pip install --upgrade pip
pip install -r requirements.txt

echo -e "${GREEN}✅ Backend setup complete${NC}"
cd ..

# Frontend Setup
echo -e "${BLUE}🎨 Setting up frontend...${NC}"
cd frontend

# Install Node dependencies
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}Installing Node packages (this may take a few minutes)...${NC}"
    npm install
else
    echo -e "${YELLOW}Updating Node packages...${NC}"
    npm install
fi

echo -e "${GREEN}✅ Frontend setup complete${NC}"
cd ..

# Make scripts executable
echo -e "${BLUE}🔐 Making scripts executable...${NC}"
chmod +x START_BOT_MAC.command
chmod +x STOP_BOT_MAC.command
chmod +x SETUP_MAC.command

echo
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}   ✅ Setup Complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo
echo -e "${YELLOW}Next steps:${NC}"
echo -e "1. Configure your Zerodha credentials in backend/.env"
echo -e "2. Run START_BOT_MAC.command to start the bot"
echo
echo -e "${YELLOW}Press any key to close...${NC}"
read -n 1
