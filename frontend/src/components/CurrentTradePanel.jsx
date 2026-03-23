import React, { useState } from 'react';
import { Paper, Typography, Box, Grid, Button, CircularProgress, LinearProgress } from '@mui/material';
import CountUp from 'react-countup';
import { useStore } from '../store/store';

export default function CurrentTradePanel({ trade, onManualExit }) {
    const [loading, setLoading] = useState(false);
    const isSpectator = useStore(state => state.isSpectatorMode);
    const params = useStore(state => state.params);

    const handleExitClick = async () => {
        setLoading(true);
        await onManualExit();
        setLoading(false);
    };

    if (!trade) {
        return (
            <Paper
                elevation={3}
                sx={{
                    p: 2,
                    background: 'linear-gradient(145deg, #ffffff 0%, #f8f9fa 100%)',
                    borderRadius: 2,
                    border: '1px solid rgba(0,0,0,0.05)'
                }}
            >
                <Typography variant="body2" sx={{ mb: 1, fontWeight: 600 }}>Current Trade</Typography>
                <Typography sx={{ fontWeight: 'bold', color: 'text.secondary' }}>STATUS: No Active Trade</Typography>
            </Paper>
        );
    }

    const pnlColor = trade.pnl >= 0 ? 'success.main' : 'error.main';
    const ltp = trade.ltp || trade.entry_price;
    const dailyPt = params?.daily_pt || 3000;
    const pnlProgress = Math.min((Math.abs(trade.pnl) / dailyPt) * 100, 100);

    return (
        <Paper
            elevation={3}
            sx={{
                p: 2,
                position: 'relative',
                background: 'linear-gradient(145deg, rgba(255,255,255,0.95) 0%, rgba(248,249,250,0.95) 100%)',
                backdropFilter: 'blur(10px)',
                borderRadius: 2,
                border: '1px solid rgba(0,0,0,0.05)',
                overflow: 'hidden',
                '&:hover': {
                    boxShadow: 6,
                    transition: 'box-shadow 0.3s ease-in-out'
                }
            }}
        >
            {/* Pulsing Active Indicator */}
            <Box
                sx={{
                    position: 'absolute',
                    top: 12,
                    right: 12,
                    width: 10,
                    height: 10,
                    borderRadius: '50%',
                    backgroundColor: 'success.main',
                    animation: 'pulse 2s infinite'
                }}
            />

            <Typography variant="body2" sx={{ mb: 2, fontWeight: 600 }}>Current Trade</Typography>

            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 'bold', color: 'primary.main' }}>{trade.symbol}</Typography>
                <Typography variant="body1" sx={{ fontWeight: 600, color: 'text.secondary' }}>
                    Entry @ {trade.entry_price.toFixed(2)}
                </Typography>
            </Box>

            <Grid container spacing={1.5} sx={{ textAlign: 'left', mb: 2 }}>
                <Grid item xs={6}>
                    <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>LTP</Typography>
                    <Typography variant="h6" sx={{ fontWeight: 'bold' }}>
                        {ltp.toFixed(2)}
                    </Typography>
                </Grid>
                <Grid item xs={6}>
                    <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>P&L</Typography>
                    <Typography variant="h6" sx={{ color: pnlColor, fontWeight: 'bold' }}>
                        ₹ <CountUp end={trade.pnl} decimals={2} duration={0.5} preserveValue />
                    </Typography>
                </Grid>
                <Grid item xs={6}>
                    <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>Trail SL</Typography>
                    <Typography variant="body1" sx={{ fontWeight: 600 }}>
                        {trade.trail_sl.toFixed(2)}
                    </Typography>
                </Grid>
                <Grid item xs={6}>
                    <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>Profit %</Typography>
                    <Typography variant="body1" sx={{ color: pnlColor, fontWeight: 600 }}>
                        <CountUp end={trade.profit_pct} decimals={2} duration={0.5} preserveValue suffix=" %" />
                    </Typography>
                </Grid>
            </Grid>

            {/* P&L Progress Bar */}
            <Box sx={{ mt: 2, mb: 2 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                        Progress to Daily Target
                    </Typography>
                    <Typography variant="caption" sx={{ fontWeight: 600, color: pnlColor }}>
                        {pnlProgress.toFixed(1)}%
                    </Typography>
                </Box>
                <LinearProgress
                    variant="determinate"
                    value={pnlProgress}
                    sx={{
                        height: 8,
                        borderRadius: 1,
                        backgroundColor: 'rgba(0,0,0,0.08)',
                        '& .MuiLinearProgress-bar': {
                            background: trade.pnl >= 0 ?
                                'linear-gradient(90deg, #10b981 0%, #059669 100%)' :
                                'linear-gradient(90deg, #ef4444 0%, #dc2626 100%)',
                            borderRadius: 1
                        }
                    }}
                />
            </Box>

            {/* 📊 ENTRY CANDLE OHLC DATA */}
            {trade.entry_candle_ohlc && (
                <Box sx={{ 
                    mt: 2, 
                    mb: 2, 
                    p: 1.5, 
                    backgroundColor: 'rgba(0,0,0,0.02)',
                    borderRadius: 1,
                    border: '1px solid rgba(0,0,0,0.08)'
                }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                        <Typography variant="caption" sx={{ fontWeight: 600, color: 'text.secondary' }}>
                            📊 Entry Candle Analysis
                        </Typography>
                        {/* Candle Status Indicator */}
                        <Box sx={{
                            px: 1,
                            py: 0.3,
                            borderRadius: 0.5,
                            backgroundColor: trade.entry_candle_ohlc.is_active ? 'rgba(16, 185, 129, 0.1)' : 'rgba(107, 114, 128, 0.1)',
                            border: trade.entry_candle_ohlc.is_active ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid rgba(107, 114, 128, 0.3)'
                        }}>
                            <Typography variant="caption" sx={{ 
                                fontWeight: 700,
                                fontSize: '0.65rem',
                                color: trade.entry_candle_ohlc.is_active ? 'success.main' : 'text.secondary'
                            }}>
                                {trade.entry_candle_ohlc.is_active ? '🟢 ACTIVE' : '🔴 CLOSED'} ({trade.entry_candle_ohlc.candle_age_sec}s)
                            </Typography>
                        </Box>
                    </Box>
                    
                    {/* Entry Quality Badge */}
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                            {trade.entry_candle_ohlc.candle_type}
                        </Typography>
                        <Box sx={{
                            px: 1,
                            py: 0.5,
                            borderRadius: 1,
                            backgroundColor: 
                                trade.entry_candle_ohlc.entry_position_pct > 85 ? 'rgba(239, 68, 68, 0.1)' :
                                trade.entry_candle_ohlc.entry_position_pct < 30 ? 'rgba(16, 185, 129, 0.1)' :
                                'rgba(234, 179, 8, 0.1)',
                            border: 
                                trade.entry_candle_ohlc.entry_position_pct > 85 ? '1px solid rgba(239, 68, 68, 0.3)' :
                                trade.entry_candle_ohlc.entry_position_pct < 30 ? '1px solid rgba(16, 185, 129, 0.3)' :
                                '1px solid rgba(234, 179, 8, 0.3)'
                        }}>
                            <Typography variant="caption" sx={{ 
                                fontWeight: 700,
                                color: 
                                    trade.entry_candle_ohlc.entry_position_pct > 85 ? 'error.main' :
                                    trade.entry_candle_ohlc.entry_position_pct < 30 ? 'success.main' :
                                    'warning.main'
                            }}>
                                {trade.entry_candle_ohlc.entry_position_pct > 85 ? '🚨 PEAK ENTRY' :
                                 trade.entry_candle_ohlc.entry_position_pct < 30 ? '🎯 EXCELLENT' :
                                 '👍 GOOD'}
                            </Typography>
                        </Box>
                    </Box>

                    {/* OHLC Values */}
                    <Grid container spacing={1} sx={{ mb: 1 }}>
                        <Grid item xs={3}>
                            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', fontSize: '0.65rem' }}>Open</Typography>
                            <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.8rem' }}>
                                ₹{trade.entry_candle_ohlc.open}
                            </Typography>
                        </Grid>
                        <Grid item xs={3}>
                            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', fontSize: '0.65rem' }}>High</Typography>
                            <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.8rem', color: 'success.main' }}>
                                ₹{trade.entry_candle_ohlc.high}
                            </Typography>
                        </Grid>
                        <Grid item xs={3}>
                            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', fontSize: '0.65rem' }}>Low</Typography>
                            <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.8rem', color: 'error.main' }}>
                                ₹{trade.entry_candle_ohlc.low}
                            </Typography>
                        </Grid>
                        <Grid item xs={3}>
                            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', fontSize: '0.65rem' }}>
                                {trade.entry_candle_ohlc.is_active ? 'LTP' : 'Close'}
                            </Typography>
                            <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.8rem' }}>
                                ₹{trade.entry_candle_ohlc.close}
                            </Typography>
                        </Grid>
                    </Grid>

                    {/* Entry Position Bar */}
                    <Box sx={{ mt: 1.5 }}>
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                            <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.65rem' }}>
                                Entry @ {trade.entry_candle_ohlc.entry_position_pct}% of candle range
                            </Typography>
                            <Typography variant="caption" sx={{ fontWeight: 600, fontSize: '0.65rem' }}>
                                {trade.entry_candle_ohlc.distance_from_high < trade.entry_candle_ohlc.range * 0.1 ? 
                                    `⚠️ ${trade.entry_candle_ohlc.distance_from_high} from high` : 
                                    `✅ ${trade.entry_candle_ohlc.distance_from_low} from low`}
                            </Typography>
                        </Box>
                        <Box sx={{ 
                            position: 'relative',
                            height: 6, 
                            borderRadius: 1,
                            background: 'linear-gradient(90deg, #10b981 0%, #eab308 50%, #ef4444 100%)'
                        }}>
                            {/* Entry marker */}
                            <Box sx={{
                                position: 'absolute',
                                left: `${trade.entry_candle_ohlc.entry_position_pct}%`,
                                top: -3,
                                width: 12,
                                height: 12,
                                backgroundColor: 'white',
                                border: '2px solid #1976d2',
                                borderRadius: '50%',
                                transform: 'translateX(-50%)',
                                boxShadow: '0 2px 4px rgba(0,0,0,0.3)'
                            }} />
                        </Box>
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.3 }}>
                            <Typography variant="caption" sx={{ fontSize: '0.6rem', color: 'text.secondary' }}>
                                Low
                            </Typography>
                            <Typography variant="caption" sx={{ fontSize: '0.6rem', color: 'text.secondary' }}>
                                High
                            </Typography>
                        </Box>
                    </Box>

                    {/* Body & Wicks */}
                    <Grid container spacing={1} sx={{ mt: 1 }}>
                        <Grid item xs={4}>
                            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', fontSize: '0.65rem' }}>Body</Typography>
                            <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.75rem' }}>
                                {trade.entry_candle_ohlc.body_pct}%
                            </Typography>
                        </Grid>
                        <Grid item xs={4}>
                            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', fontSize: '0.65rem' }}>Upper Wick</Typography>
                            <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.75rem' }}>
                                ₹{trade.entry_candle_ohlc.upper_wick}
                            </Typography>
                        </Grid>
                        <Grid item xs={4}>
                            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', fontSize: '0.65rem' }}>Lower Wick</Typography>
                            <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.75rem' }}>
                                ₹{trade.entry_candle_ohlc.lower_wick}
                            </Typography>
                        </Grid>
                    </Grid>
                </Box>
            )}

            <Button
                fullWidth
                variant="contained"
                color="error"
                onClick={handleExitClick}
                disabled={loading || isSpectator}
                sx={{
                    fontWeight: 600,
                    py: 1,
                    '&:hover': {
                        transform: 'translateY(-2px)',
                        boxShadow: 4
                    },
                    transition: 'all 0.2s ease-in-out'
                }}
            >
                {loading ? <CircularProgress size={24} color="inherit" /> : 'Manual Exit Trade'}
            </Button>
        </Paper>
    );
}