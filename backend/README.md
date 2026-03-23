# Trading Bot Backend

This is the FastAPI backend for a multi-strategy algorithmic trading bot. It handles broker authentication with Zerodha Kite, real-time market data processing via WebSockets, trade execution, persistent logging, and communication with the React frontend.

## ✨ Features

- **Broker Integration**: Secure authentication and session management with Zerodha Kite API.
- **Real-time Data**: Ingests live market ticks using Kite's WebSocket Ticker.
- **Multi-Strategy Engine**: Implements several trading strategies:
  - RSI Crossover with Angle Confirmation
  - MA Crossover Anticipation
  - Candlestick Pattern Recognition (Engulfing, Hammer, Doji, etc.)
  - Trend Continuation
  - Unusual Options Activity (UOA) Scanner
- **Risk Management**: Automated calculation of trade quantity based on capital and user-defined risk per trade.
- **Position Management**: Handles entries, partial profit-taking, and trailing stop-losses.
- **Persistent Logging**: All trades are logged to a SQLite database for long-term performance analysis.
- **Parameter Optimization**: Includes a module to analyze historical performance and suggest parameter adjustments.
- **Frontend Communication**: Pushes real-time status, trade updates, and chart data to the UI via WebSockets.

## 🛠️ Tech Stack

- **Framework**: FastAPI
- **Server**: Uvicorn, Gunicorn
- **Broker API**: KiteConnect
- **Data Handling**: Pandas, NumPy
- **Database**: SQLite
- **Async**: asyncio

## 📋 Prerequisites

- Python 3.9+
- Pip (Python Package Installer)

## ⚙️ Setup and Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repository-url>
    cd <your-repository-name>/backend
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    # For Linux/macOS
    python3 -m venv venv
    source venv/bin/activate

    # For Windows
    python -m venv venv
    .\venv\Scripts\activate
    ```

3.  **Install the required packages:**
    ```bash
    pip install -r requirements.txt
    ```

## 📝 Configuration

The application requires API credentials from Zerodha Kite.

1.  Create a file named `.env` in the `backend` directory.
2.  Add your credentials to this file as follows:

    ```env
    # .env file
    API_KEY="your_kite_api_key"
    API_SECRET="your_kite_api_secret"
    ```

    *These credentials will be loaded automatically on startup. The `.env` file is included in `.gitignore` to prevent accidental commits.*

## 🚀 Running the Application

To run the backend server for development, use the following command from the `backend` directory:

```bash
uvicorn main:app --reload --port 8000