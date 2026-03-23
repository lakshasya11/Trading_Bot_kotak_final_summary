import React, { useEffect, useRef, useCallback } from 'react';
import { Howl } from 'howler';
import { Grid, ThemeProvider, CssBaseline, Box, Typography, Chip, Alert } from '@mui/material';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import AccessTimeIcon from '@mui/icons-material/AccessTime';
import ErrorBoundary from './ErrorBoundary.jsx';
import StatusPanel from './components/StatusPanel';
import ParametersPanel from './components/ParametersPanel';
import IntelligencePanel from './components/IntelligencePanel';
import NetPerformancePanel from './components/NetPerformancePanel';
import CurrentTradePanel from './components/CurrentTradePanel';
import IndexChart from './components/IndexChart';
import OptionChain from './components/OptionChain';
import LogTabs from './components/LogTabs';
import StraddleMonitor from './components/StraddleMonitor';
import TrendDirectionScoutPanel from './components/TrendDirectionScoutPanel';
import UserSelector from './components/UserSelector';
import { createSocketConnection } from './services/socket';
import { manualExit, getTradeHistory, getTradeHistoryAll, logout } from './services/api';
import { getBackendURL } from './services/getBackendURL';
import { useStore } from './store/store';
import { useSnackbar } from 'notistack';
import tradingTheme from './theme';

const MOCK_MODE = false;

// Create sounds ONCE outside component to prevent audio pool exhaustion
const sounds = {
    entry: new Howl({ src: ['/sound/entry.mp3'], volume: 0.7, html5: true, pool: 1 }),
    profit: new Howl({ src: ['/sound/profit.mp3'], volume: 0.7, html5: true, pool: 1 }),
    loss: new Howl({ src: ['/sound/loss.mp3'], volume: 0.7, html5: true, pool: 1 }),
    warning: new Howl({ src: ['/sound/warning.mp3'], volume: 1.0, html5: true, pool: 1 }),
};

// Unlock audio on first user interaction (browser autoplay policy)
let audioUnlocked = false;
const unlockAudio = () => {
    if (!audioUnlocked) {
        // Play and immediately stop to unlock browser audio
        // Use a small delay before stopping to ensure proper unlock
        Object.values(sounds).forEach(sound => {
            const id = sound.play();
            setTimeout(() => sound.stop(id), 100);
        });
        audioUnlocked = true;
        console.log('🔓 Audio unlocked for autoplay');
        document.removeEventListener('click', unlockAudio);
        document.removeEventListener('touchstart', unlockAudio);
    }
};
document.addEventListener('click', unlockAudio);
document.addEventListener('touchstart', unlockAudio);

function App() {

    const { enqueueSnackbar } = useSnackbar();
    const socketRef = useRef(null);
    const reconnectTimerRef = useRef(null);
    const pingIntervalRef = useRef(null);
    const pongTimeoutRef = useRef(null);
    const lastPongRef = useRef(Date.now());
    const isConnectingRef = useRef(false);
    const reconnectAttemptsRef = useRef(0);
    const maxReconnectAttempts = 10;
    const isUnmountingRef = useRef(false);
    const connectionIdRef = useRef(0);
    const lastServerTimeRef = useRef(null);
    const lastServerTimeReceivedRef = useRef(null);

    // Debug: Try to get store state
    let storeState;
    try {
        storeState = useStore();
        console.log("✅ Store state retrieved:", storeState);
    } catch (error) {
        console.error("❌ Error getting store state:", error);
        return <div style={{ padding: '20px', color: 'red' }}>Store Error: {error.message}</div>;
    }

    const {
        botStatus, dailyPerformance, currentTrade, debugLogs,
        optionChain, chartData, socketStatus, trendData, expiryInfo, activeUser
    } = storeState;

    // Debug: Log to console to verify App is rendering
    console.log("📊 App component rendering with data:", { botStatus, socketStatus });

    const sendSocketMessage = useCallback((message) => {
        if (socketRef.current?.readyState === WebSocket.OPEN) {
            socketRef.current.send(JSON.stringify(message));
        } else {
            console.error("Cannot send message, WebSocket is not open.");
        }
    }, []);

    useEffect(() => {
        const { getState, setState } = useStore;

        const connect = async () => {
            // Prevent multiple simultaneous connection attempts
            if (isConnectingRef.current) {
                console.log('⚠️ Connection attempt already in progress, skipping...');
                return;
            }

            // Check if already connected
            if (socketRef.current?.readyState === WebSocket.OPEN) {
                console.log('✅ Already connected, skipping reconnection');
                return;
            }

            // Check max reconnection attempts
            if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
                console.error('❌ Max reconnection attempts reached. Manual refresh required.');
                setState({ socketStatus: 'FAILED' });
                enqueueSnackbar('Connection failed. Please refresh the page.', { variant: 'error', persist: true });
                return;
            }

            // Check if component is unmounting
            if (isUnmountingRef.current) {
                console.log('🛑 Component unmounting, skipping connection');
                return;
            }

            const currentConnectionId = ++connectionIdRef.current;
            console.log(`🔌 Starting connection attempt #${reconnectAttemptsRef.current + 1} (ID: ${currentConnectionId})`);

            isConnectingRef.current = true;
            setState({ socketStatus: 'CONNECTING' });

            const handleOpen = async () => {
                // Validate this is still the active connection
                if (isUnmountingRef.current) {
                    console.log('🛑 Component unmounting, closing new connection');
                    if (socketRef.current) socketRef.current.close();
                    return;
                }

                console.log(`✅ WebSocket connected successfully (ID: ${currentConnectionId})`);
                setState({ socketStatus: 'CONNECTED' });
                isConnectingRef.current = false;
                reconnectAttemptsRef.current = 0; // Reset reconnect attempts on successful connection

                try {
                    console.log('🔄 Fetching trade history...');
                    const [todayHistory, allTimeHistory] = await Promise.all([
                        getTradeHistory(),
                        getTradeHistoryAll()
                    ]);

                    console.log('📥 Trade history received:', {
                        todayCount: todayHistory?.length || 0,
                        allTimeCount: allTimeHistory?.length || 0,
                        todayData: todayHistory
                    });

                    getState().setTradeHistory(todayHistory);
                    getState().setAllTimeTradeHistory(allTimeHistory);

                    console.log(`✅ Loaded ${todayHistory?.length || 0} trades from today.`);
                    console.log(`✅ Loaded ${allTimeHistory?.length || 0} trades from all-time history.`);

                    // Calculate daily performance from trade history for status bar
                    if (todayHistory && todayHistory.length > 0) {
                        const dailyStats = todayHistory.reduce((acc, trade) => {
                            // FIX: Prioritize net_pnl over pnl (gross) to match backend broadcasts
                            const tradeNetPnl = trade.net_pnl || 0;
                            const tradeGrossPnl = trade.pnl || 0;
                            const tradeCharges = trade.charges || 0;

                            console.log(`💰 Trade: ${trade.symbol || 'N/A'}, Gross: ${tradeGrossPnl}, Charges: ${tradeCharges}, Net: ${tradeNetPnl}`);

                            return {
                                trades_today: acc.trades_today + 1,
                                grossPnl: acc.grossPnl + tradeGrossPnl,  // Add grossPnl for panel
                                totalCharges: acc.totalCharges + tradeCharges,  // Add charges for panel
                                netPnl: acc.netPnl + tradeNetPnl,  // Add netPnl for panel
                                net_pnl: acc.net_pnl + tradeNetPnl,  // Keep net_pnl alias for backward compatibility
                                wins: acc.wins + (tradeNetPnl > 0 ? 1 : 0),  // Rename to 'wins' for consistency
                                losses: acc.losses + (tradeNetPnl < 0 ? 1 : 0),  // Rename to 'losses' for consistency
                                winning_trades: acc.winning_trades + (tradeNetPnl > 0 ? 1 : 0),
                                losing_trades: acc.losing_trades + (tradeNetPnl < 0 ? 1 : 0)
                            };
                        }, { trades_today: 0, grossPnl: 0, totalCharges: 0, netPnl: 0, net_pnl: 0, wins: 0, losses: 0, winning_trades: 0, losing_trades: 0 });

                        console.log('📊 Calculated daily stats:', dailyStats);
                        getState().updateDailyPerformance(dailyStats);
                        console.log(`✅ Status bar updated: ${dailyStats.trades_today} trades, Net P&L: ₹${dailyStats.netPnl.toFixed(2)}`);
                    } else {
                        console.warn('⚠️ No trades found for today, status bar will show 0');
                    }
                } catch (error) {
                    console.error('❌ Failed to load trade history:', error);
                    // Silently fail - empty trade history is normal on startup
                }

                if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
                if (pongTimeoutRef.current) clearTimeout(pongTimeoutRef.current);

                lastPongRef.current = Date.now();

                pingIntervalRef.current = setInterval(() => {
                    if (socketRef.current?.readyState === WebSocket.OPEN) {
                        const timeSinceLastPong = Date.now() - lastPongRef.current;

                        // Only disconnect if no pong received for 90 seconds (much more lenient)
                        if (timeSinceLastPong > 90000) {
                            console.warn('⚠️ No pong received for 90 seconds, connection may be dead');
                            if (socketRef.current) {
                                socketRef.current.close(1000, 'Ping timeout');
                            }
                            return;
                        }

                        // Send ping
                        try {
                            socketRef.current.send(JSON.stringify({ type: 'ping' }));
                        } catch (error) {
                            console.error('❌ Failed to send ping:', error);
                        }
                    }
                }, 30000); // Send ping every 30 seconds (reduced frequency) 
            };

            const handleMessage = (event) => {
                // ANY message from server resets the heartbeat timer
                lastServerTimeReceivedRef.current = Date.now();
                
                try {
                    const data = JSON.parse(event.data);

                    // 🔍 Enhanced error logging with try-catch per case to prevent silent failures
                    const handleCase = (caseType, handler) => {
                        try {
                            handler();
                        } catch (error) {
                            console.error(`❌ Error processing ${caseType}:`, error, 'Payload:', data.payload);
                            // Continue processing other messages instead of crashing
                        }
                    };

                    switch (data.type) {
                        case 'status_update':
                            handleCase('status_update', () => {
                                // Extract Trend Direction Scout data if present
                                if (data.payload.trend_direction_data) {
                                    getState().updateTrendData(data.payload.trend_direction_data);
                                }

                                // Handle delta updates (merge with existing state)
                                if (data.is_delta) {
                                    const currentBotStatus = getState().botStatus;
                                    getState().updateBotStatus({ ...currentBotStatus, ...data.payload });
                                } else {
                                    getState().updateBotStatus(data.payload);
                                }
                            });
                            break;

                        // Legacy clock sync handlers disabled - now using batch_frame_update
                        case 'time_sync':
                        case 'market_time_sync':
                            console.warn('⚠️ Received legacy clock sync message - should use batch_frame_update');
                            break;

                        case 'batch_frame_update':
                            handleCase('batch_frame_update', () => {
                                // 🎯 PROFESSIONAL 30 FPS BATCHED UPDATE: Single message with all data
                                // This matches Bloomberg Terminal / TradingView architecture
                                const framePayload = data.payload;

                                // Update clock (always present)
                                if (framePayload.timestamp) {
                                    lastServerTimeRef.current = framePayload.timestamp;
                                    lastServerTimeReceivedRef.current = Date.now();
                                }

                                // Update status (connection, mode, index, trend)
                                if (framePayload.status) {
                                    const currentBotStatus = getState().botStatus;
                                    getState().updateBotStatus({
                                        ...currentBotStatus,
                                        ...framePayload.status,
                                        current_time: framePayload.timestamp,
                                        timezone: framePayload.timezone
                                    });

                                    // 📐 Extract Trend Direction Scout data from status if present
                                    if (framePayload.status.trend_direction_data) {
                                        getState().updateTrendData(framePayload.status.trend_direction_data);
                                    }
                                }

                                // Update performance (P&L, wins, losses)
                                if (framePayload.performance) {
                                    getState().updateDailyPerformance(framePayload.performance);
                                }

                                // Update trade status (position, P&L)
                                if (framePayload.trade !== undefined) {
                                    getState().updateCurrentTrade(framePayload.trade);
                                }

                                // Update conflict prices (if any ticks this frame)
                                if (framePayload.prices) {
                                    // Prices are already conflated - update data manager
                                    // This could update option chain prices in real-time
                                    console.debug('Frame prices:', Object.keys(framePayload.prices).length);
                                }

                                // Update expiry info (available expiries, selection)
                                if (framePayload.expiry_info) {
                                    getState().updateExpiryInfo(framePayload.expiry_info);
                                }
                            });
                            break;

                        case 'daily_performance_update':
                            handleCase('daily_performance_update', () => getState().updateDailyPerformance(data.payload));
                            break;
                        case 'trade_status_update':
                            handleCase('trade_status_update', () => getState().updateCurrentTrade(data.payload));
                            break;
                        case 'debug_log':
                            handleCase('debug_log', () => getState().addDebugLog(data.payload));
                            break;
                        case 'debug_log_batch':
                            handleCase('debug_log_batch', () => {
                                // Handle batched debug logs (up to 10 logs at once)
                                data.payload.logs.forEach(log => getState().addDebugLog(log));
                                if (data.payload.dropped > 0) {
                                    console.warn(`⚠️ ${data.payload.dropped} debug logs were dropped due to rate limiting`);
                                }
                            });
                            break;
                        case 'new_trade_log':
                            handleCase('new_trade_log', () => {
                                const trade = data.payload;
                                console.log(`📝 TRADE RECEIVED from backend:`, {
                                    symbol: trade?.symbol,
                                    entry_price: trade?.entry_price,
                                    exit_price: trade?.exit_price,
                                    pnl: trade?.pnl,
                                    net_pnl: trade?.net_pnl,
                                    timestamp: trade?.timestamp,
                                    exit_reason: trade?.exit_reason
                                });
                                const newState = getState().tradeHistory.length;
                                getState().addTradeToHistory(trade);
                                const updatedState = getState().tradeHistory.length;
                                console.log(`✅ Trade added to history. Count: ${newState} → ${updatedState}`);
                            });
                            break;
                        case 'option_chain_update':
                            handleCase('option_chain_update', () => getState().updateOptionChain(data.payload));
                            break;
                        case 'uoa_list_update':
                            handleCase('uoa_list_update', () => getState().updateUoaList(data.payload));
                            break;
                        case 'chart_data_update':
                            handleCase('chart_data_update', () => getState().updateChartData(data.payload));
                            break;
                        case 'expiry_info_update':
                            handleCase('expiry_info_update', () => getState().updateExpiryInfo(data.payload));
                            break;
                        // ADDED: Handle straddle monitor updates
                        case 'straddle_update':
                            handleCase('straddle_update', () => getState().updateStraddleData(data.payload));
                            break;
                        case 'active_user_update':
                            handleCase('active_user_update', () => getState().updateActiveUser(data.payload));
                            break;
                        case 'play_sound':
                            handleCase('play_sound', () => {
                                console.log(`🔊 Sound request received: ${data.payload}`);
                                if (sounds[data.payload]) {
                                    sounds[data.payload].play()
                                        .then(() => console.log(`✅ Sound played: ${data.payload}`))
                                        .catch(err => console.error(`❌ Sound play failed: ${data.payload}`, err));
                                } else {
                                    console.error(`❌ Sound not found: ${data.payload}`);
                                }
                            });
                            break;
                        case 'pong':
                            lastPongRef.current = Date.now();
                            break;
                        // ADDED: Handle system warnings like open positions
                        case 'system_warning':
                            handleCase('system_warning', () => {
                                enqueueSnackbar(data.payload.message, {
                                    variant: 'warning',
                                    persist: true,
                                });
                            });
                            break;
                        case 'logout_notification':
                            handleCase('logout_notification', () => {
                                enqueueSnackbar(data.payload.message, { variant: 'info', autoHideDuration: 3000 });
                                setTimeout(() => {
                                    window.location.href = 'http://localhost:3001';
                                }, 1500);
                            });
                            break;
                    }
                } catch (error) {
                    console.error("❌ Critical error in WebSocket message handler:", error, "Raw data:", event.data);
                }
            };

            const handleClose = (event) => {
                console.log(`🔌 WebSocket closed (ID: ${currentConnectionId}):`, event.code, event.reason);

                // Don't reconnect if component is unmounting or intentionally closed
                if (isUnmountingRef.current) {
                    console.log('🛑 Component unmounting, not reconnecting');
                    setState({ socketStatus: 'DISCONNECTED' });
                    isConnectingRef.current = false;
                    return;
                }

                // Check if this was a clean close (1000) or intentional
                const wasCleanClose = event.code === 1000;
                if (wasCleanClose && event.reason === 'Component unmounting') {
                    console.log('🛑 Clean shutdown, not reconnecting');
                    setState({ socketStatus: 'DISCONNECTED' });
                    isConnectingRef.current = false;
                    return;
                }

                setState({ socketStatus: 'DISCONNECTED' });
                isConnectingRef.current = false;

                // Clear intervals
                if (pingIntervalRef.current) {
                    clearInterval(pingIntervalRef.current);
                    pingIntervalRef.current = null;
                }
                if (pongTimeoutRef.current) {
                    clearTimeout(pongTimeoutRef.current);
                    pongTimeoutRef.current = null;
                }

                // Check if we've exceeded max reconnection attempts
                if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
                    console.error('❌ Max reconnection attempts reached');
                    setState({ socketStatus: 'FAILED' });
                    return;
                }

                // Exponential backoff for reconnection
                reconnectAttemptsRef.current++;
                const baseDelay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current - 1), 30000);
                const jitter = Math.random() * 1000; // Add jitter to prevent thundering herd
                const delay = baseDelay + jitter;

                console.log(`🔄 Will reconnect in ${(delay / 1000).toFixed(1)}s (attempt ${reconnectAttemptsRef.current}/${maxReconnectAttempts})`);

                if (reconnectTimerRef.current) {
                    clearTimeout(reconnectTimerRef.current);
                }

                reconnectTimerRef.current = setTimeout(() => {
                    if (!isConnectingRef.current && !isUnmountingRef.current) {
                        connect();
                    }
                }, delay);
            };

            // Clean up existing connections and timers
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current);
                reconnectTimerRef.current = null;
            }
            if (pingIntervalRef.current) {
                clearInterval(pingIntervalRef.current);
                pingIntervalRef.current = null;
            }
            if (pongTimeoutRef.current) {
                clearTimeout(pongTimeoutRef.current);
                pongTimeoutRef.current = null;
            }

            // Close existing socket if not already closed
            if (socketRef.current) {
                const currentState = socketRef.current.readyState;
                if (currentState !== WebSocket.CLOSED && currentState !== WebSocket.CLOSING) {
                    console.log('🔌 Closing existing connection before creating new one');
                    try {
                        socketRef.current.close(1000, 'Reconnecting');
                    } catch (error) {
                        console.warn('⚠️ Error closing existing socket:', error);
                    }
                }
                socketRef.current = null;
            }

            // Small delay to ensure old connection is fully closed
            await new Promise(resolve => setTimeout(resolve, 100));

            try {
                socketRef.current = createSocketConnection(
                    handleOpen,
                    handleMessage,
                    handleClose,
                    (error) => {
                        console.error('❌ Socket error:', error);
                        isConnectingRef.current = false;
                        setState({ socketStatus: 'DISCONNECTED' });
                        // Error will trigger close event, which handles reconnection
                    }
                );
                console.log(`🔌 WebSocket connection created (ID: ${currentConnectionId})`);
            } catch (error) {
                console.error('❌ Failed to create WebSocket connection:', error);
                isConnectingRef.current = false;
                setState({ socketStatus: 'DISCONNECTED' });

                // Schedule reconnection on creation failure
                reconnectAttemptsRef.current++;
                const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current - 1), 30000);
                console.log(`🔄 Will retry connection in ${delay / 1000}s`);
                reconnectTimerRef.current = setTimeout(() => {
                    if (!isUnmountingRef.current) connect();
                }, delay);
            }
        };

        if (!MOCK_MODE) connect();

        return () => {
            console.log('🧹 Cleaning up WebSocket connections...');
            isUnmountingRef.current = true;
            isConnectingRef.current = false;

            // Clear all timers
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current);
                reconnectTimerRef.current = null;
            }
            if (pingIntervalRef.current) {
                clearInterval(pingIntervalRef.current);
                pingIntervalRef.current = null;
            }
            if (pongTimeoutRef.current) {
                clearTimeout(pongTimeoutRef.current);
                pongTimeoutRef.current = null;
            }

            // Close socket cleanly
            if (socketRef.current) {
                const state = socketRef.current.readyState;
                if (state !== WebSocket.CLOSED && state !== WebSocket.CLOSING) {
                    try {
                        socketRef.current.close(1000, 'Component unmounting');
                    } catch (error) {
                        console.warn('⚠️ Error closing socket on unmount:', error);
                    }
                }
                socketRef.current = null;
            }
            console.log('✅ WebSocket cleanup complete');
        };
    }, [MOCK_MODE, enqueueSnackbar]);

    // Handle page visibility changes to maintain WebSocket connection
    useEffect(() => {
        const handleVisibilityChange = async () => {
            if (document.hidden) {
                console.log('📱 Page hidden - WebSocket will continue in background');
                // Don't disconnect - let it continue in background
            } else {
                console.log('👁️ Page visible - performing full state resync');

                // 🔄 FULL STATE RESYNC: Refresh all critical state in parallel when window regains focus
                // This ensures P&L and trade list are immediately in sync even if events were missed while hidden.
                // Note: current trade position and prices are kept up-to-date by the WebSocket stream (batch_frame_update).
                try {
                    const backendURL = 'http://localhost:8000';
                    const [perfRes, historyRes] = await Promise.allSettled([
                        fetch(`${backendURL}/api/performance`),
                        fetch(`${backendURL}/api/trade_history`),
                    ]);

                    // Refresh P&L / wins / losses
                    if (perfRes.status === 'fulfilled' && perfRes.value.ok) {
                        const perfData = await perfRes.value.json();
                        useStore.getState().updateDailyPerformance(perfData);
                    }

                    // Refresh today's completed trade history list
                    if (historyRes.status === 'fulfilled' && historyRes.value.ok) {
                        const historyData = await historyRes.value.json();
                        useStore.getState().setTradeHistory(historyData);
                    }

                    console.log('✅ Full state resync complete on visibility change');
                } catch (error) {
                    console.error('⚠️ State resync failed:', error);
                }

                // Only reconnect if truly disconnected and not already attempting
                const socket = socketRef.current;
                if (!socket || socket.readyState === WebSocket.CLOSED) {
                    if (!isConnectingRef.current && !isUnmountingRef.current) {
                        console.log('🔄 WebSocket disconnected while page was hidden, initiating reconnection...');
                        // Reset reconnection attempts for user-initiated reconnection
                        reconnectAttemptsRef.current = 0;
                        // Don't close - socket is already closed, just trigger new connection
                        // The existing useEffect will handle reconnection
                    }
                } else if (socket.readyState === WebSocket.OPEN) {
                    console.log('✅ WebSocket still connected after page visibility change');
                    // Send a ping to ask backend to push fresh state immediately
                    try {
                        socket.send(JSON.stringify({ type: 'ping' }));
                    } catch (error) {
                        console.error('❌ Failed to send ping after visibility change:', error);
                    }
                }
            }
        };

        document.addEventListener('visibilitychange', handleVisibilityChange);

        return () => {
            document.removeEventListener('visibilitychange', handleVisibilityChange);
        };
    }, []);

    // Separate useEffect to fetch trade history on mount
    useEffect(() => {
        const fetchTradeHistory = async () => {
            const { getState, setState } = useStore;

            // Load from localStorage first (instant display)
            try {
                const savedTodayTrades = localStorage.getItem('tradeHistory');
                const savedAllTimeTrades = localStorage.getItem('allTimeTradeHistory');
                
                if (savedTodayTrades) {
                    const trades = JSON.parse(savedTodayTrades);
                    getState().setTradeHistory(trades);
                    console.log('✅ Loaded', trades.length, 'trades from localStorage (today)');
                }
                
                if (savedAllTimeTrades) {
                    const trades = JSON.parse(savedAllTimeTrades);
                    getState().setAllTimeTradeHistory(trades);
                    console.log('✅ Loaded', trades.length, 'trades from localStorage (all-time)');
                }
            } catch (error) {
                console.error('Failed to load trades from localStorage:', error);
            }

            try {
                console.log('🔄 Fetching trade history from API...');
                const [todayHistory, allTimeHistory] = await Promise.all([
                    getTradeHistory(),
                    getTradeHistoryAll()
                ]);

                console.log('📥 Trade history received:', {
                    todayCount: todayHistory?.length || 0,
                    allTimeCount: allTimeHistory?.length || 0,
                    todayData: todayHistory
                });

                console.log('📊 Sample all-time data:', allTimeHistory?.slice(0, 2));

                // ✅ CRITICAL FIX: Ensure trades are set in store with quota protection
                const state = getState();
                try {
                    state.setTradeHistory(todayHistory);
                } catch (e) {
                    console.error('Failed to set trade history:', e);
                }
                try {
                    state.setAllTimeTradeHistory(allTimeHistory);
                } catch (e) {
                    console.error('Failed to set all-time trade history:', e);
                }

                // ✅ VERIFY: Check that trades are now in store
                const updatedState = getState();
                console.log('✅ Store AFTER update:', {
                    tradeHistoryLength: updatedState.tradeHistory?.length || 0,
                    allTimeLength: updatedState.allTimeTradeHistory?.length || 0
                });

                console.log(`✅ Loaded ${todayHistory?.length || 0} trades from today.`);

                // Calculate daily performance from trade history for status bar
                if (todayHistory && todayHistory.length > 0) {
                    const dailyStats = todayHistory.reduce((acc, trade) => {
                        // FIX: Prioritize net_pnl over pnl (gross) to match backend broadcasts
                        const tradeNetPnl = trade.net_pnl || 0;
                        const tradeGrossPnl = trade.pnl || 0;
                        const tradeCharges = trade.charges || 0;

                        console.log(`💰 Trade: ${trade.symbol || 'N/A'}, Gross: ${tradeGrossPnl}, Charges: ${tradeCharges}, Net: ${tradeNetPnl}`);

                        return {
                            trades_today: acc.trades_today + 1,
                            grossPnl: acc.grossPnl + tradeGrossPnl,  // Add grossPnl for panel
                            totalCharges: acc.totalCharges + tradeCharges,  // Add charges for panel
                            netPnl: acc.netPnl + tradeNetPnl,  // Add netPnl for panel
                            net_pnl: acc.net_pnl + tradeNetPnl,  // Keep net_pnl alias for backward compatibility
                            wins: acc.wins + (tradeNetPnl > 0 ? 1 : 0),  // Rename to 'wins' for consistency
                            losses: acc.losses + (tradeNetPnl < 0 ? 1 : 0),  // Rename to 'losses' for consistency
                            winning_trades: acc.winning_trades + (tradeNetPnl > 0 ? 1 : 0),
                            losing_trades: acc.losing_trades + (tradeNetPnl < 0 ? 1 : 0)
                        };
                    }, { trades_today: 0, grossPnl: 0, totalCharges: 0, netPnl: 0, net_pnl: 0, wins: 0, losses: 0, winning_trades: 0, losing_trades: 0 });

                    console.log('📊 Calculated daily stats:', dailyStats);
                    getState().updateDailyPerformance(dailyStats);
                    console.log(`✅ Status bar updated: ${dailyStats.trades_today} trades, Net P&L: ₹${dailyStats.netPnl.toFixed(2)}`);
                } else {
                    console.warn('⚠️ No trades found for today, status bar will show 0');
                }
            } catch (error) {
                console.error('❌ Failed to load trade history:', error);
                // Silently fail - backend may not be running yet or no trades exist
            }
        };

        if (!MOCK_MODE) {
            fetchTradeHistory();
        }
    }, [MOCK_MODE, enqueueSnackbar]);

    // Fetch active user on mount
    useEffect(() => {
        const fetchActiveUser = async () => {
            try {
                const response = await fetch('http://localhost:8000/api/users/active');
                if (response.ok) {
                    const userData = await response.json();
                    useStore.getState().updateActiveUser(userData);
                    console.log('✅ Active user loaded:', userData.name);
                } else if (response.status === 404) {
                    // No user_profiles.json or no active user - using .env
                    console.log('ℹ️ Using .env credentials (no user_profiles.json)');
                    useStore.getState().updateActiveUser(null);
                }
            } catch (error) {
                console.log('ℹ️ Could not fetch active user (backend may not be running yet)');
                // Don't show error - this is not critical
            }
        };

        fetchActiveUser();
    }, []);

    // Client-side clock interpolation for smooth updates
    useEffect(() => {
        let clockInterval;

        const updateClock = () => {
            if (lastServerTimeRef.current && lastServerTimeReceivedRef.current) {
                try {
                    // Calculate elapsed time since last server update
                    const elapsedMs = Date.now() - lastServerTimeReceivedRef.current;

                    // Parse server time (HH:MM:SS.mmm)
                    const [time, ms] = lastServerTimeRef.current.split('.');
                    const [hours, minutes, seconds] = time.split(':').map(Number);

                    // Add elapsed time (continue indefinitely, no timeout cutoff)
                    const totalMs = (hours * 3600 + minutes * 60 + seconds) * 1000 + parseInt(ms || 0) + elapsedMs;
                    const newSeconds = Math.floor(totalMs / 1000) % 86400; // Wrap at 24 hours
                    const newMs = totalMs % 1000;

                    const h = Math.floor(newSeconds / 3600);
                    const m = Math.floor((newSeconds % 3600) / 60);
                    const s = newSeconds % 60;

                    const interpolatedTime = `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${newMs.toString().padStart(3, '0').substring(0, 3)}`;

                    // Update UI smoothly - auto-corrects when server updates arrive
                    const { getState } = useStore;
                    const currentStatus = getState().botStatus;
                    getState().updateBotStatus({ ...currentStatus, current_time: interpolatedTime });
                } catch (error) {
                    console.error('Clock interpolation error:', error);
                }
            }
        };

        // Handle page visibility changes
        const handleVisibilityChange = () => {
            if (!document.hidden) {
                // Tab became visible - trigger immediate clock update
                updateClock();
            }
        };

        document.addEventListener('visibilitychange', handleVisibilityChange);
        clockInterval = setInterval(updateClock, 50); // Update every 50ms (20 FPS)

        return () => {
            clearInterval(clockInterval);
            document.removeEventListener('visibilitychange', handleVisibilityChange);
        };
    }, []);

    // 🚨 HEARTBEAT MONITOR: Auto-reconnect if no batch_frame_update messages for 5 seconds
    useEffect(() => {
        const heartbeatInterval = setInterval(() => {
            if (lastServerTimeReceivedRef.current && socketStatus === 'CONNECTED') {
                const timeSinceLastUpdate = Date.now() - lastServerTimeReceivedRef.current;

                // If no updates for 10 seconds, connection is frozen - force reconnect
                if (timeSinceLastUpdate > 10000) {
                    console.error(`🚨 HEARTBEAT FAILED: No updates for ${(timeSinceLastUpdate / 1000).toFixed(1)}s - forcing reconnect`);

                    // Mark as disconnected
                    const { setState } = useStore;
                    setState({ socketStatus: 'DISCONNECTED' });

                    // Close existing socket to trigger reconnection
                    if (socketRef.current) {
                        try {
                            socketRef.current.close(4000, 'Heartbeat timeout - no updates received');
                        } catch (error) {
                            console.warn('⚠️ Error closing frozen socket:', error);
                        }
                    }

                    // Reset tracking
                    lastServerTimeReceivedRef.current = null;
                    lastServerTimeRef.current = null;
                }
            }
        }, 2000); // Check every 2 seconds

        return () => clearInterval(heartbeatInterval);
    }, [socketStatus]);

    const handleManualExit = async () => {
        if (window.confirm('Are you sure you want to manually exit the current trade?')) {
            try {
                const data = await manualExit();
                enqueueSnackbar(data.message, { variant: 'warning' });
            } catch (error) {
                enqueueSnackbar(error.message, { variant: 'error' });
            }
        }
    };

    // Debug: Add a safety check
    if (!botStatus) {
        console.warn("⚠️ botStatus is undefined, using defaults");
    }

    return (
        <ThemeProvider theme={tradingTheme}>
            <CssBaseline />
            <Box sx={{ minHeight: '100vh', bgcolor: 'background.default' }}>
                {/* Enhanced Status Bar */}
                <Box
                    sx={{
                        position: 'sticky',
                        top: 0,
                        zIndex: 1000,
                        background: socketStatus === 'CONNECTED' ?
                            'linear-gradient(135deg, #10b981 0%, #059669 100%)' :
                            'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)',
                        color: 'white',
                        py: 1,
                        px: 2,
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
                    }}
                >
                    <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
                        <Chip
                            icon={<FiberManualRecordIcon sx={{ fontSize: 12 }} />}
                            label={`Socket: ${socketStatus || 'DISCONNECTED'}`}
                            size="small"
                            sx={{
                                bgcolor: 'rgba(255,255,255,0.2)',
                                color: 'white',
                                fontWeight: 600,
                                '& .MuiChip-icon': { color: 'white' }
                            }}
                        />
                        <Chip
                            label={`Mode: ${botStatus?.mode || 'UNKNOWN'}`}
                            size="small"
                            sx={{
                                bgcolor: botStatus?.mode?.includes('PAPER') ? 'rgba(59,130,246,0.9)' : 'rgba(251,191,36,0.9)',
                                color: 'white',
                                fontWeight: 600
                            }}
                        />
                        <Chip
                            label={`Trades: ${dailyPerformance?.trades_today || 0}`}
                            size="small"
                            sx={{
                                bgcolor: 'rgba(255,255,255,0.2)',
                                color: 'white',
                                fontWeight: 600
                            }}
                        />
                        <Chip
                            label={`P&L: ₹${(dailyPerformance?.net_pnl || 0).toFixed(2)}`}
                            size="small"
                            sx={{
                                bgcolor: (dailyPerformance?.net_pnl || 0) >= 0 ?
                                    'rgba(16,185,129,0.9)' : 'rgba(239,68,68,0.9)',
                                color: 'white',
                                fontWeight: 600
                            }}
                        />
                        <Chip
                            icon={<AccessTimeIcon sx={{ fontSize: 16 }} />}
                            label={`${botStatus?.current_time || '--:--:--'} IST`}
                            size="small"
                            sx={{
                                bgcolor: 'rgba(102, 126, 234, 0.9)',
                                color: 'white',
                                fontFamily: 'monospace',
                                fontWeight: 600,
                                fontSize: '0.85rem'
                            }}
                        />
                    </Box>

                    {botStatus?.kill_switch_active && (
                        <Alert
                            severity="error"
                            sx={{
                                py: 0,
                                px: 1,
                                bgcolor: 'rgba(255,255,255,0.95)',
                                '& .MuiAlert-icon': { fontSize: 18 }
                            }}
                        >
                            ⚠️ KILL SWITCH ACTIVE
                        </Alert>
                    )}
                </Box>

                <Box sx={{ p: 2 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                        <Box>
                            <Typography variant="h4" sx={{ color: 'primary.main', fontWeight: 'bold' }}>
                                🤖 Trading Bot Dashboard
                            </Typography>
                            <Typography variant="body2" sx={{ color: 'text.secondary', mt: 0.5 }}>
                                {botStatus?.connection || 'DISCONNECTED'} • {botStatus?.indexName || 'INDEX'}: {botStatus?.indexPrice?.toFixed(2) ?? '0.00'}
                            </Typography>
                        </Box>
                        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
                            <ErrorBoundary name="UserSelector">
                                <UserSelector />
                            </ErrorBoundary>
                            <button
                                onClick={async () => {
                                    if (window.confirm('Are you sure you want to logout? This will stop the bot and disconnect all sessions.')) {
                                        try {
                                            const response = await logout();
                                            enqueueSnackbar(response.message || 'Logout successful', { variant: 'success', autoHideDuration: 3000 });
                                            setTimeout(() => { window.location.href = 'http://localhost:3001'; }, 1500);
                                        } catch (error) {
                                            enqueueSnackbar(error.message || 'Logout completed with errors', { variant: 'warning', autoHideDuration: 3000 });
                                            setTimeout(() => { window.location.href = 'http://localhost:3001'; }, 2000);
                                        }
                                    }
                                }}
                                style={{
                                    background: 'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)',
                                    color: 'white', border: 'none', padding: '8px 16px',
                                    borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold'
                                }}
                            >
                                Logout
                            </button>
                        </Box>
                    </Box>
                    <Grid container spacing={2}>
                        <Grid item xs={12} md={4} container direction="column" spacing={2} wrap="nowrap">
                            <ErrorBoundary name="StatusPanel"><Grid item><StatusPanel status={botStatus} socketStatus={socketStatus} expiryInfo={expiryInfo} activeUser={activeUser} /></Grid></ErrorBoundary>
                            <ErrorBoundary name="CurrentTradePanel"><Grid item><CurrentTradePanel trade={currentTrade} onManualExit={handleManualExit} /></Grid></ErrorBoundary>
                            <ErrorBoundary name="TrendDirectionScout"><Grid item><TrendDirectionScoutPanel trendData={trendData} /></Grid></ErrorBoundary>
                            <ErrorBoundary name="ParametersPanel"><Grid item><ParametersPanel isMock={MOCK_MODE} /></Grid></ErrorBoundary>
                            <ErrorBoundary name="IntelligencePanel"><Grid item><IntelligencePanel /></Grid></ErrorBoundary>
                            <ErrorBoundary name="StraddleMonitor"><Grid item><StraddleMonitor /></Grid></ErrorBoundary>
                            <ErrorBoundary name="NetPerformancePanel"><Grid item><NetPerformancePanel data={dailyPerformance} /></Grid></ErrorBoundary>
                        </Grid>
                        <Grid item xs={12} md={8} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            <ErrorBoundary name="IndexChart"><Box><IndexChart data={chartData} /></Box></ErrorBoundary>
                            <ErrorBoundary name="OptionChain"><Box><OptionChain data={optionChain} /></Box></ErrorBoundary>
                            <ErrorBoundary name="LogTabs"><Box sx={{ flexGrow: 1, minHeight: 0 }}><LogTabs debugLogs={debugLogs} /></Box></ErrorBoundary>
                        </Grid>
                    </Grid>
                </Box>
            </Box>
        </ThemeProvider>
    );
}

export default App;

