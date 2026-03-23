#!/bin/bash

echo "========================================"
echo "   V47.14 Trading Bot - Linux/Mac Setup"
echo "========================================"
echo

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: Python 3 is not installed${NC}"
    echo "Please install Python 3.8+ using your package manager"
    echo "Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
    echo "CentOS/RHEL: sudo yum install python3 python3-pip"
    echo "macOS: brew install python3"
    exit 1
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo -e "${RED}ERROR: Node.js is not installed${NC}"
    echo "Please install Node.js from https://nodejs.org"
    echo "Or use a package manager:"
    echo "Ubuntu/Debian: sudo apt install nodejs npm"
    echo "CentOS/RHEL: sudo yum install nodejs npm"
    echo "macOS: brew install node"
    exit 1
fi

echo -e "${GREEN}✅ Python and Node.js found${NC}"
echo

# Create Python virtual environment
echo -e "${BLUE}📦 Creating Python virtual environment...${NC}"
cd backend
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}✅ Virtual environment created${NC}"
else
    echo -e "${GREEN}✅ Virtual environment already exists${NC}"
fi

# Activate virtual environment and install Python dependencies
echo -e "${BLUE}📦 Installing Python dependencies...${NC}"
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to install Python dependencies${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Python dependencies installed${NC}"

# Install auto-login dependencies (optional but recommended)
echo -e "${BLUE}📦 Installing auto-login dependencies (Selenium, PyOTP, WebDriver Manager)...${NC}"
pip install selenium pyotp webdriver-manager
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}⚠️ Warning: Failed to install auto-login dependencies${NC}"
    echo "Some auto-login features may not work, but basic trading will function"
else
    echo -e "${GREEN}✅ Auto-login dependencies installed${NC}"
fi

# Deactivate virtual environment
deactivate
cd ..

# Install Node.js dependencies
echo -e "${BLUE}📦 Installing Node.js dependencies...${NC}"
cd frontend
npm install
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to install Node.js dependencies${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Node.js dependencies installed${NC}"
cd ..

# Create configuration files if they don't exist
echo -e "${BLUE}🔧 Setting up configuration files...${NC}"
if [ ! -f "backend/access_token.json" ]; then
    cat > backend/access_token.json << EOF
{
  "access_token": "YOUR_ACCESS_TOKEN_HERE",
  "user_id": "YOUR_USER_ID_HERE"
}
EOF
    echo -e "${GREEN}✅ Created access_token.json template${NC}"
fi

if [ ! -f "backend/user_profiles.json" ]; then
    if [ -f "backend/user_profiles.json.template" ]; then
        cp backend/user_profiles.json.template backend/user_profiles.json
        echo -e "${GREEN}✅ Created user_profiles.json from template (auto-login config)${NC}"
    else
        echo -e "${YELLOW}⚠️ user_profiles.json.template not found - auto-login will be skipped${NC}"
    fi
fi

if [ ! -f "backend/strategy_params.json" ] && [ -f "backend/strategy_params.json.template" ]; then
    cp backend/strategy_params.json.template backend/strategy_params.json
    echo -e "${GREEN}✅ Created strategy_params.json from template${NC}"
fi

# Make scripts executable
chmod +x START_BOT_LINUX.sh
chmod +x STOP_BOT_LINUX.sh

echo
echo "========================================"
echo "       AUTO-LOGIN SETUP (OPTIONAL)"
echo "========================================"
echo "To enable one-click bot startup with auto-login:"
echo
echo "1. Edit backend/user_profiles.json"
echo "2. Fill in these fields:"
echo "   - user_id: Your Zerodha client ID"
echo "   - password: Your Zerodha password"
echo "   - totp_secret: Your 2FA secret key"
echo "   - api_key: Your Kite API key"
echo "   - api_secret: Your Kite API secret"
echo
echo "See AUTO_LOGIN_SETUP_GUIDE.md for detailed instructions"
echo
echo "========================================"
echo
echo "NEXT STEPS:"
echo "1. Edit backend/user_profiles.json for auto-login (recommended)"
echo "2. Or use backend/access_token.json if you already have a token"
echo "3. Review backend/strategy_params.json for trading parameters"
echo "4. Run ./START_BOT_LINUX.sh to launch the trading bot"
echo "5. Access the web interface at http://localhost:3000"