import React, { useState, useEffect, useCallback } from 'react';
import { Paper, Typography, Grid, TextField, Select, MenuItem, Button, FormControl, InputLabel, CircularProgress, Box, Checkbox, FormControlLabel } from '@mui/material';
import { useSnackbar } from 'notistack';
import { useStore } from '../store/store';
import { getStatus, authenticate, startBot, stopBot, pauseBot, unpauseBot, updateStrategyParams } from '../services/api';

// Generate default expiries for immediate use (before API fetch completes)
const generateDefaultExpiries = (indexName) => {
    const expiries = [];
    const today = new Date();
    
    // Determine expiry day: SENSEX = Thursday, NIFTY = Tuesday, BANKNIFTY = Thursday (monthly)
    const expiryDay = indexName === 'NIFTY' ? 2 : 4; // Tuesday = 2, Thursday = 4
    const isMonthlyOnly = indexName === 'BANKNIFTY';
    
    // Generate next 12-16 weeks of expiries
    const weeksToGenerate = isMonthlyOnly ? 4 : 16; // BANKNIFTY only needs 4 months
    
    for (let week = 0; week < weeksToGenerate; week++) {
        const futureDate = new Date(today);
        
        if (isMonthlyOnly) {
            // For BANKNIFTY: last Thursday of each month
            const month = today.getMonth() + week;
            const year = today.getFullYear() + Math.floor(month / 12);
            const adjustedMonth = month % 12;
            
            // Get last day of month
            const lastDay = new Date(year, adjustedMonth + 1, 0);
            
            // Find last Thursday
            let lastThursday = lastDay;
            while (lastThursday.getDay() !== 4) {
                lastThursday.setDate(lastThursday.getDate() - 1);
            }
            
            if (lastThursday > today) {
                expiries.push(formatDate(lastThursday));
            }
        } else {
            // For NIFTY/SENSEX: weekly expiries
            futureDate.setDate(today.getDate() + (week * 7));
            
            // Adjust to the correct expiry day
            const dayOffset = (expiryDay - futureDate.getDay() + 7) % 7;
            futureDate.setDate(futureDate.getDate() + dayOffset);
            
            expiries.push(formatDate(futureDate));
        }
    }
    
    return expiries.filter(exp => exp >= formatDate(today)).slice(0, isMonthlyOnly ? 4 : 12);
};

const formatDate = (date) => {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
};

export default function ParametersPanel({ isMock = false }) {
    const { enqueueSnackbar } = useSnackbar();
    
    const isSpectator = useStore(state => state.isSpectatorMode);
    const isBotRunning = useStore(state => state.botStatus.is_running);
    const isPaused = useStore(state => state.botStatus.is_paused);
    const params = useStore(state => state.params);
    const expiryInfo = useStore(state => state.expiryInfo); // Listen to WebSocket expiry updates
    const updateParam = useStore(state => state.updateParam);

    const [auth, setAuth] = useState({ status: 'loading', login_url: '', user: '' });
    const [reqToken, setReqToken] = useState('');
    const [availableExpiries, setAvailableExpiries] = useState([]);
    const [loadingExpiries, setLoadingExpiries] = useState(false);
    const [usingDefaultExpiries, setUsingDefaultExpiries] = useState(true);
    
    const [isStartLoading, setIsStartLoading] = useState(false);
    const [isStopLoading, setIsStopLoading] = useState(false);
    const [isPauseLoading, setIsPauseLoading] = useState(false);

    // Initialize with default expiries immediately (no waiting!)
    useEffect(() => {
        const selectedIndex = params.selectedIndex || 'NIFTY';
        const defaults = generateDefaultExpiries(selectedIndex);
        setAvailableExpiries(defaults);
        setUsingDefaultExpiries(true);
        
        // Auto-set expiry if current value is old format
        const currentExpiry = params.option_expiry_type;
        if (!currentExpiry || ['CURRENT_WEEK', 'NEXT_WEEK', 'MONTHLY'].includes(currentExpiry)) {
            if (defaults.length > 0) {
                updateParam('option_expiry_type', defaults[0]);
            }
        }
    }, [params.selectedIndex]);

    // Listen to WebSocket expiry updates from bot (when bot starts and loads instruments)
    useEffect(() => {
        if (expiryInfo && expiryInfo.available_expiries && expiryInfo.available_expiries.length > 0) {
            console.log('📅 Received real expiries from bot:', expiryInfo.available_expiries.length);
            setAvailableExpiries(expiryInfo.available_expiries);
            setUsingDefaultExpiries(false);
            setLoadingExpiries(false);
        }
    }, [expiryInfo]);

    const fetchStatus = useCallback(async (retries = 0) => {
        try {
            const data = await getStatus();
            setAuth(data);
        } catch (error) {
            if (retries < 10) {
                // Backend may still be starting — retry silently
                setTimeout(() => fetchStatus(retries + 1), 2000);
            } else {
                setAuth({ status: 'error', login_url: '' });
                enqueueSnackbar('Failed to connect to the backend server.', { variant: 'error' });
            }
        }
    }, [enqueueSnackbar]);

    useEffect(() => {
        if (isMock) { setAuth({ status: 'authenticated' }); return; }
        fetchStatus(0);
    }, [isMock, fetchStatus]);

    // Fetch real expiries from API in background (replaces defaults when ready)
    useEffect(() => {
        const fetchRealExpiries = async () => {
            const selectedIndex = params.selectedIndex || 'NIFTY';
            setLoadingExpiries(true);
            try {
                // Fetch in background - don't block user
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 180000); // 3 minutes for slow scripmaster downloads
                
                const response = await fetch(`http://localhost:8000/api/expiries/${selectedIndex}`, {
                    signal: controller.signal
                });
                clearTimeout(timeoutId);
                
                if (response.ok) {
                    const data = await response.json();
                    if (data.expiries && data.expiries.length > 0) {
                        setAvailableExpiries(data.expiries);
                        setUsingDefaultExpiries(false);
                        
                        // If current expiry is not in real list, update to first real expiry
                        const currentExpiry = params.option_expiry_type;
                        if (currentExpiry && !data.expiries.includes(currentExpiry)) {
                            updateParam('option_expiry_type', data.expiries[0]);
                        }
                    }
                } else {
                    // Keep using defaults if API fails
                    console.warn('Failed to fetch real expiries, using defaults');
                }
            } catch (error) {
                // Keep using defaults if API fails
                if (error.name !== 'AbortError') {
                    console.warn('Error fetching real expiries, using defaults:', error);
                }
            } finally {
                setLoadingExpiries(false);
            }
        };

        // Only fetch if we have a valid index and not in mock mode
        if (params.selectedIndex && !isMock) {
            fetchRealExpiries();
        }
    }, [params.selectedIndex, isMock]);

    const handleManualAuthenticate = async () => {
        if (!reqToken.trim()) {
            enqueueSnackbar('Please paste the request token from Kite.', { variant: 'warning' });
            return;
        }
        setIsStartLoading(true);
        try {
            const data = await authenticate(reqToken);
            enqueueSnackbar('Authentication successful!', { variant: 'success' });
            setAuth({ status: 'authenticated', user: data.user, login_url: '' });
        } catch (error) {
            enqueueSnackbar(error.message, { variant: 'error' });
            await fetchStatus();
        }
        setIsStartLoading(false);
    };

    const handleChange = async (e) => {
        const { name, value, type, checked } = e.target;
        const newValue = type === 'checkbox' ? checked : value;
        updateParam(name, newValue);
        
        // Auto-update Supertrend parameters to strategy if bot is running
        if (isBotRunning && (name === 'supertrend_period' || name === 'supertrend_multiplier')) {
            try {
                await updateStrategyParams({ [name]: newValue });
                enqueueSnackbar(`${name === 'supertrend_period' ? 'Period' : 'Multiplier'} updated to ${newValue}`, { variant: 'success' });
            } catch (error) {
                console.error('Failed to update strategy parameters:', error);
                enqueueSnackbar('Failed to update Supertrend settings', { variant: 'error' });
            }
        }
    };    const handleStart = async () => {
        setIsStartLoading(true);
        try {
            // Show immediate feedback
            enqueueSnackbar('Starting bot... (~10-15 seconds)', { 
                variant: 'info',
                autoHideDuration: 15000
            });
            
            const data = await startBot(params, params.selectedIndex);
            enqueueSnackbar(data.message || 'Bot started successfully!', { variant: 'success' });
        } catch (error) {
            enqueueSnackbar(error.message || 'Failed to start bot', { variant: 'error' });
        }
        setIsStartLoading(false);
    };

    const handleStop = async () => {
        setIsStopLoading(true);
        try {
            // Show immediate feedback
            enqueueSnackbar('Stopping bot... (5-10 seconds)', { 
                variant: 'info',
                autoHideDuration: 10000
            });
            
            if (isBotRunning) {
                const data = await stopBot();
                if (data?.success) {
                    setStatus(data.status);
                    enqueueSnackbar('Bot stopped successfully!', { variant: 'success' });
                    // Pause state will be reset via WebSocket status updates
                }
            }
        } catch (error) {
            console.error('Error stopping bot:', error);
            enqueueSnackbar('Failed to stop bot: ' + error.message, { variant: 'error' });
        } finally {
            setIsStopLoading(false);
        }
    };

    const handlePause = async () => {
        setIsPauseLoading(true);
        try {
            await (isPaused ? unpauseBot() : pauseBot());
            // Pause state will be synced via WebSocket status updates
        } catch (error) {
            console.error('Error pausing/unpausing bot:', error);
        } finally {
            setIsPauseLoading(false);
        }
    };    if (auth.status === 'loading') {
        return <Paper sx={{ p: 2, textAlign: 'center' }}><CircularProgress /></Paper>;
    }
    
    if (auth.status !== 'authenticated' && !isBotRunning) {
        return (
            <Paper elevation={3} sx={{ p: 2 }}>
                <Typography variant="h6" sx={{mb: 2}}>Authentication Required</Typography>
                <Button fullWidth variant="contained" href={auth.login_url} target="_blank" disabled={!auth.login_url}>Login with Kite</Button>
                <TextField fullWidth margin="normal" label="Paste Request Token here" value={reqToken} onChange={e => setReqToken(e.target.value)} variant="outlined" size="small"/>
                <Button fullWidth variant="contained" color="primary" sx={{ mt: 1 }} onClick={handleManualAuthenticate} disabled={isStartLoading || !reqToken}>
                    {isStartLoading ? <CircularProgress size={24} /> : 'Authenticate'}
                </Button>
            </Paper>
        );
    }
    
    const fields = [
        { label: 'Select Index', name: 'selectedIndex', type: 'select', options: ['NIFTY', 'BANKNIFTY', 'SENSEX'] },
        { label: 'Option Expiry', name: 'option_expiry_type', type: 'select', options: availableExpiries, loading: loadingExpiries },
        { label: 'Trading Mode', name: 'trading_mode', type: 'select', options: ['Paper Trading', 'Live Trading'] },
        { label: 'Capital', name: 'start_capital', type: 'number' },
        { label: 'SL (Points)', name: 'trailing_sl_points', type: 'number' },
        { label: 'SL (%)', name: 'trailing_sl_percent', type: 'number' },
        { label: 'Daily SL (₹)', name: 'daily_sl', type: 'number' },
        { label: 'Daily PT (₹)', name: 'daily_pt', type: 'number' },
        { label: 'Trade PT (₹)', name: 'trade_profit_target', type: 'number' },
        { label: 'BE %', name: 'break_even_threshold_pct', type: 'number' },
        { label: 'Partial Profit %', name: 'partial_profit_pct', type: 'number'},
        { label: 'Partial Exit %', name: 'partial_exit_pct', type: 'number'},
        { label: 'Supertrend Period', name: 'supertrend_period', type: 'number'},
        { label: 'Supertrend Multiplier', name: 'supertrend_multiplier', type: 'number', step: '0.1'},
        // � PAPER TRADING REALISM: Simulate live trading delays
        { label: '📄 Paper Entry Delay (ms)', name: 'paper_entry_delay_ms', type: 'number', step: '10'},
        { label: '📄 Paper Exit Delay (ms)', name: 'paper_exit_delay_ms', type: 'number', step: '10'},
        { label: '📄 Paper Verification Delay (ms)', name: 'paper_verification_delay_ms', type: 'number', step: '10'},
        // �🟢 GREEN CANDLE HOLD OVERRIDE
        { label: 'Green Candle Min Profit (%)', name: 'green_hold_min_profit_pct', type: 'number', step: '0.1'},
        { label: 'Green Candle Max Loss (%)', name: 'green_hold_max_loss_pct', type: 'number', step: '0.1'},
        // REMOVED: Stop Loss (₹) and Profit Target (₹) - using Trailing SL (points/%) and Trade PT instead
        // { label: 'Stop Loss (₹)', name: 'stop_loss', type: 'number' },
        // { label: 'Profit Target (₹)', name: 'profit_target', type: 'number' },
        // REMOVED: Recovery and Max Qty are no longer used by the backend logic
        // { label: 'Re-entry Thresh (%)', name: 'recovery_threshold_pct', type: 'number' },
        // { label: 'Max Qty / Order', name: 'max_lots_per_order', type: 'number' },
        // REMOVED: Volatility parameters are no longer used by the backend logic
        // { label: 'Vol Circuit Breaker (%)', name: 'vol_circuit_breaker_pct', type: 'number' },
        // { label: 'Max Vol for Reversal (%)', name: 'max_vol_for_reversal_pct', type: 'number' },
        // { label: 'Min Vol for Trend (%)', name: 'min_vol_for_trend_pct', type: 'number' },
    ];

    return (
        <Paper elevation={3} sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 2 }}>Parameters (User: {auth.user})</Typography>
            <Grid container spacing={2}>
                {fields.map(field => (
                    <Grid item xs={12} key={field.name}>
                        {field.type === 'select' ? (
                            <FormControl fullWidth size="small">
                                <InputLabel>{field.label}</InputLabel>
                                <Select 
                                    name={field.name} 
                                    value={params[field.name] || ''} 
                                    label={field.label} 
                                    onChange={handleChange} 
                                    disabled={isBotRunning || isSpectator || (field.name === 'option_expiry_type' && field.loading)}
                                >
                                    {field.name === 'option_expiry_type' && loadingExpiries ? (
                                        <MenuItem disabled>Loading real expiries...</MenuItem>
                                    ) : (
                                        field.options.map(opt => <MenuItem key={opt} value={opt}>{opt}</MenuItem>)
                                    )}
                                </Select>
                                {field.name === 'option_expiry_type' && usingDefaultExpiries && !loadingExpiries && (
                                    <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, fontSize: '0.7rem' }}>
                                        ℹ️ Estimated dates. Start bot to load exact expiries from exchange.
                                    </Typography>
                                )}
                                {field.name === 'option_expiry_type' && !usingDefaultExpiries && (
                                    <Typography variant="caption" color="success.main" sx={{ mt: 0.5, fontSize: '0.7rem' }}>
                                        ✅ Live expiries from exchange
                                    </Typography>
                                )}
                            </FormControl>
                        ) : (
                            <TextField name={field.name} label={field.label} type="number" value={params[field.name] || ''} onChange={handleChange} size="small" fullWidth disabled={isBotRunning || isSpectator} inputProps={field.step ? { step: field.step } : {}} />
                        )}
                    </Grid>
                ))}
                <Grid item xs={12}>
                    <FormControlLabel control={<Checkbox name="auto_scan_uoa" checked={!!params.auto_scan_uoa} onChange={handleChange} disabled={isBotRunning || isSpectator} />} label="Enable Auto-Scan for UOA" />
                </Grid>
                <Grid item xs={12}>
                    <FormControlLabel control={<Checkbox name="green_candle_hold_enabled" checked={!!params.green_candle_hold_enabled} onChange={handleChange} disabled={isBotRunning || isSpectator} />} label="🟢 Enable Green Candle Hold Override" />
                </Grid>
            </Grid>
            <Box sx={{ mt: 2, display: 'flex', gap: 1 }}>
                <Button
                    fullWidth
                    variant="contained"
                    color="success"
                    onClick={handleStart}
                    disabled={isStartLoading || isBotRunning || isSpectator}
                    sx={{ minHeight: 42 }}
                >
                    {isStartLoading ? (
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <CircularProgress size={20} color="inherit" />
                            <span>Starting...</span>
                        </Box>
                    ) : 'Start Bot'}
                </Button>
                <Button
                    fullWidth
                    variant="contained"
                    color={isPaused ? "secondary" : "warning"}
                    onClick={handlePause}
                    disabled={isPauseLoading || !isBotRunning || isSpectator}
                    sx={{ minHeight: 42 }}
                >
                    {isPauseLoading ? <CircularProgress size={24} color="inherit" /> : (isPaused ? 'Resume' : 'Pause')}
                </Button>
                <Button
                    fullWidth
                    variant="contained"
                    color="error"
                    onClick={handleStop}
                    disabled={isStopLoading || !isBotRunning || isSpectator}
                    sx={{ minHeight: 42 }}
                >
                    {isStopLoading ? (
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <CircularProgress size={20} color="inherit" />
                            <span>Stopping...</span>
                        </Box>
                    ) : 'Stop Bot'}
                </Button>
            </Box>
        </Paper>
    );
}
