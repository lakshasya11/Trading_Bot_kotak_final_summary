import { create } from 'zustand';

const spectatorFlag = !!import.meta.env.VITE_MASTER_BACKEND_URL;

const initialRealtimeState = {
    chartData: null,
    botStatus: { connection: 'DISCONNECTED', mode: 'NOT STARTED', indexPrice: 0, trend: '---', indexName: 'INDEX', is_running: false, is_paused: false },
    dailyPerformance: { grossPnl: 0, totalCharges: 0, netPnl: 0, wins: 0, losses: 0 },
    currentTrade: null,
    debugLogs: [],
    tradeHistory: [],
    allTimeTradeHistory: [], 
    optionChain: [],
    uoaList: [],
    straddleData: null,
    premiumVelocity: {},  // 💰 NEW: {symbol: {currentVelocity, pctVelocity, acceleration, trend, velocityMa, ...}}
    trendData: null,  // 📊 Trend direction scout: {atm_strike, ce_option, pe_option, recommendation, confidence}
    expiryInfo: null,  // 📅 Expiry information: {available_expiries, selected_expiry_type, current_expiry}
    activeUser: null,  // 👤 Active user: {id, name, description}
    socketStatus: 'DISCONNECTED',
};

// ===== Parameters Slice =====
const createParametersSlice = (set) => ({
    params: {},
    loadParams: () => {
        const savedParams = localStorage.getItem('tradingParams');
        const defaultParams = {
            selectedIndex: 'NIFTY', option_expiry_type: '', trading_mode: 'Paper Trading',
            start_capital: 50000, trailing_sl_points: 5, 
            trailing_sl_percent: 2.5, daily_sl: -20000, daily_pt: 40000, 
            trade_profit_target: 1000, break_even_threshold_pct: 2.0, partial_profit_pct: 3, partial_exit_pct: 30, auto_scan_uoa: false,
            green_candle_hold_enabled: false, green_hold_min_profit_pct: 1.0, green_hold_max_loss_pct: -2.0,
            supertrend_period: 5, supertrend_multiplier: 0.7,
            // REMOVED: stop_loss and profit_target - using Trailing SL (points/%) and Trade PT instead
            // REMOVED: Recovery and Max Lots are no longer needed here as they are not used in the simplified logic
            // recovery_threshold_pct: 2.0, 
            // max_lots_per_order: 1800
        };
        set({ params: savedParams ? { ...defaultParams, ...JSON.parse(savedParams) } : defaultParams });
    },
    setParams: (newParams) => {
        localStorage.setItem('tradingParams', JSON.stringify(newParams));
        set({ params: newParams });
    },
    updateParam: (name, value) => set((state) => {
        const updatedParams = { ...state.params, [name]: value };
        localStorage.setItem('tradingParams', JSON.stringify(updatedParams));
        return { params: updatedParams };
    }),
});

// ===== Real-time Data Slice =====
const createRealtimeDataSlice = (set) => ({
    ...initialRealtimeState,
    resetRealtimeData: () => set(initialRealtimeState),
    isSpectatorMode: spectatorFlag,
    setSocketStatus: (status) => set({ socketStatus: status }),
    setTradeHistory: (history) => {
        console.log('📝 Setting tradeHistory:', history?.length || 0, 'trades');
        if (Array.isArray(history)) {
            try {
                localStorage.setItem('tradeHistory', JSON.stringify(history));
            } catch (e) {
                if (e.name === 'QuotaExceededError') {
                    console.warn('⚠️ localStorage quota exceeded for tradeHistory, keeping only recent 100 trades');
                    const recent = history.slice(0, 100);
                    localStorage.setItem('tradeHistory', JSON.stringify(recent));
                }
            }
        }
        set({ tradeHistory: Array.isArray(history) ? history : [] });
    },
    setAllTimeTradeHistory: (history) => {
        console.log('📝 Setting allTimeTradeHistory:', history?.length || 0, 'trades');
        if (Array.isArray(history)) {
            try {
                localStorage.setItem('allTimeTradeHistory', JSON.stringify(history));
            } catch (e) {
                if (e.name === 'QuotaExceededError') {
                    console.warn('⚠️ localStorage quota exceeded for allTimeTradeHistory, keeping only recent 50 trades');
                    const recent = history.slice(0, 50);
                    localStorage.setItem('allTimeTradeHistory', JSON.stringify(recent));
                }
            }
        }
        set({ allTimeTradeHistory: Array.isArray(history) ? history : [] });
    },
    updateBotStatus: (payload) => set({ botStatus: payload }),
    updateDailyPerformance: (payload) => set({ dailyPerformance: payload }),
    updateCurrentTrade: (payload) => set({ currentTrade: payload }),
    addDebugLog: (payload) => set(state => ({ debugLogs: [payload, ...state.debugLogs].slice(0, 500) })),
    updateOptionChain: (payload) => set({ optionChain: payload }),
    updateUoaList: (payload) => set({ uoaList: payload }),
    updateChartData: (payload) => set({ chartData: payload }),
    updateStraddleData: (payload) => set({ straddleData: payload }),
    updateTrendData: (payload) => set({ trendData: payload }),  // 📊 Trend direction update
    updateExpiryInfo: (payload) => set({ expiryInfo: payload }),  // 📅 Expiry information update
    updateActiveUser: (payload) => set({ activeUser: payload }),  // 👤 Active user update
    addTradeToHistory: (trade) => set(state => {
        console.log(`📊 STORE UPDATE: Adding trade to history:`, {
            symbol: trade?.symbol,
            pnl: trade?.net_pnl || trade?.pnl,
            timestamp: trade?.timestamp,
            totalTrades: (state.tradeHistory?.length || 0) + 1,
            trade
        });
        
        const newTradeHistory = [trade, ...state.tradeHistory];
        const newAllTimeHistory = [trade, ...state.allTimeTradeHistory];
        
        // ✅ Persist to localStorage with quota protection
        try {
            localStorage.setItem('tradeHistory', JSON.stringify(newTradeHistory));
        } catch (e) {
            if (e.name === 'QuotaExceededError') {
                console.warn('⚠️ localStorage quota exceeded, keeping only recent 100 trades');
                localStorage.setItem('tradeHistory', JSON.stringify(newTradeHistory.slice(0, 100)));
            }
        }
        
        try {
            localStorage.setItem('allTimeTradeHistory', JSON.stringify(newAllTimeHistory));
        } catch (e) {
            if (e.name === 'QuotaExceededError') {
                console.warn('⚠️ localStorage quota exceeded, keeping only recent 50 trades');
                localStorage.setItem('allTimeTradeHistory', JSON.stringify(newAllTimeHistory.slice(0, 50)));
            }
        }
        
        return {
            tradeHistory: newTradeHistory,
            allTimeTradeHistory: newAllTimeHistory
        };
    }),
    updatePremiumVelocity: (payload) => set(state => ({
        premiumVelocity: {
            ...state.premiumVelocity,
            [payload.symbol]: {
                optionType: payload.option_type,              // ← NEW: CE or PE
                atmStrike: payload.atm_strike,                // ← NEW: ATM strike price
                currentVelocity: payload.current_velocity,
                pctVelocity: payload.pct_velocity,
                acceleration: payload.acceleration,
                trend: payload.trend,
                velocityMa: payload.velocity_ma,
                indexVelocity: payload.index_velocity,
                velocityRatio: payload.velocity_ratio,
                signalType: payload.signal_type,
                currentPrice: payload.current_price,
                timestamp: payload.timestamp
            }
        }
    })),
    clearPremiumVelocity: (payload) => set(state => {
        // Keep only ATM symbols, remove all others
        const atmSymbols = payload.atm_symbols || [];
        const newVelocity = {};
        
        atmSymbols.forEach(symbol => {
            if (state.premiumVelocity[symbol]) {
                newVelocity[symbol] = state.premiumVelocity[symbol];
            }
        });
        
        return { premiumVelocity: newVelocity };
    }),
});

export const useStore = create((...a) => ({
    ...createParametersSlice(...a),
    ...createRealtimeDataSlice(...a),
}));

useStore.getState().loadParams();

