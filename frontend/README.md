# Trading Bot Frontend Dashboard

This is a React-based user interface for the algorithmic trading bot. It provides a real-time dashboard for authenticating, configuring parameters, controlling the bot, and visualizing all trading activity.

## ✨ Features

- **Real-time Status**: Live panels display connection status, trading mode, index price, trend, and daily P&L.
- **Interactive Financial Charts**: Renders candlestick charts with WMA, SMA, and a separate synchronized RSI pane using TradingView's Lightweight Charts library.
- **Full Bot Control**: Securely start, stop, and manually exit trades directly from the UI.
- **Dynamic Parameter Configuration**: All user-defined strategy parameters can be configured before starting the bot. Changes are saved locally.
- **Live Option Chain**: Displays a color-coded option chain that updates in real-time.
- **Tabbed Log Viewer**: Separates high-level Trade History from detailed Debug Logs for clarity.
- **In-depth Analytics**: A dedicated panel fetches and analyzes the complete trade history to display key performance metrics like Profit Factor, Max Drawdown, and an Equity Curve chart.
- **Audio & Visual Alerts**: Provides instant notifications (via Notistack) and sound alerts (via Howler) for important trade events like entries and exits.

## 🛠️ Tech Stack

- **Framework**: React 18
- **Build Tool**: Vite
- **UI Components**: Material-UI (MUI)
- **Charting**: TradingView Lightweight Charts
- **State Management**: React Hooks (`useState`, `useEffect`, `useRef`)
- **Notifications**: Notistack
- **Audio**: Howler.js

## 📋 Prerequisites

- Node.js (v18 or newer recommended)
- npm (usually comes with Node.js)

## ⚙️ Setup and Installation

1.  **Navigate to the frontend directory:**
    ```bash
    cd <your-repository-name>/frontend
    ```

2.  **Install the required packages:**
    ```bash
    npm install
    ```

## 🚀 Running the Application

To run the frontend development server, use the following command from the `frontend` directory:

```bash
npm run dev