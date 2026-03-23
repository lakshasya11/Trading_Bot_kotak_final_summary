#!/bin/bash

echo "========================================"
echo "   V47.14 Trading Bot - System Check"
echo "========================================"
echo

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}🔍 Checking system requirements...${NC}"
echo

missing=0

# Check Python
echo -e "${BLUE}[1/4] Checking Python...${NC}"
if command -v python3 &> /dev/null; then
    version=$(python3 --version 2>&1)
    echo -e "${GREEN}✅ Python found: $version${NC}"
else
    echo -e "${RED}❌ Python 3 not found - Please install Python 3.8+${NC}"
    missing=1
fi

# Check Node.js
echo -e "${BLUE}[2/4] Checking Node.js...${NC}"
if command -v node &> /dev/null; then
    version=$(node --version 2>&1)
    echo -e "${GREEN}✅ Node.js found: $version${NC}"
else
    echo -e "${RED}❌ Node.js not found - Please install Node.js from https://nodejs.org${NC}"
    missing=1
fi

# Check npm
echo -e "${BLUE}[3/4] Checking npm...${NC}"
if command -v npm &> /dev/null; then
    version=$(npm --version 2>&1)
    echo -e "${GREEN}✅ npm found: $version${NC}"
else
    echo -e "${RED}❌ npm not found - Usually comes with Node.js${NC}"
    missing=1
fi

# Check pip
echo -e "${BLUE}[4/4] Checking pip...${NC}"
if command -v pip3 &> /dev/null; then
    version=$(pip3 --version 2>&1 | cut -d' ' -f2)
    echo -e "${GREEN}✅ pip found: $version${NC}"
elif command -v pip &> /dev/null; then
    version=$(pip --version 2>&1 | cut -d' ' -f2)
    echo -e "${GREEN}✅ pip found: $version${NC}"
else
    echo -e "${RED}❌ pip not found - Usually comes with Python${NC}"
    missing=1
fi

echo
if [ $missing -eq 1 ]; then
    echo -e "${RED}❌ SYSTEM CHECK FAILED${NC}"
    echo "Please install missing requirements and run this check again"
    echo
    echo "Installation commands:"
    echo "• Ubuntu/Debian: sudo apt update && sudo apt install python3 python3-pip nodejs npm"
    echo "• CentOS/RHEL: sudo yum install python3 python3-pip nodejs npm"
    echo "• macOS: brew install python3 node"
else
    echo -e "${GREEN}✅ SYSTEM CHECK PASSED${NC}"
    echo "Your system is ready for V47.14 Trading Bot!"
    echo "Run ./SETUP_LINUX.sh to continue setup."
fi

echo