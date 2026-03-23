import React, { useEffect, useState } from 'react';
import { Paper, Typography, Box, Grid, Chip } from '@mui/material';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import { useStore } from '../store/store';

export default function PremiumVelocityPanel() {
    const premiumVelocity = useStore(state => state.premiumVelocity);
    const currentTrade = useStore(state => state.currentTrade);
    const [ceSymbol, setCeSymbol] = useState(null);
    const [peSymbol, setPeSymbol] = useState(null);

    // Extract CE and PE symbols from current trade
    useEffect(() => {
        if (currentTrade && currentTrade.symbol) {
            const symbol = currentTrade.symbol;
            // Extract strike from CE symbol (e.g., "NIFTY50FEB25C25850" -> "25850" and "NIFTY50FEB25P25850")
            const cePart = symbol.substring(0, symbol.lastIndexOf('C'));
            const strike = symbol.substring(symbol.lastIndexOf('C') + 1);
            const basePart = symbol.substring(0, symbol.lastIndexOf('C'));
            
            setCeSymbol(symbol);
            setPeSymbol(basePart + 'P' + strike);
        }
    }, [currentTrade]);

    // Get signal type emoji
    const getSignalEmoji = (signalType) => {
        switch(signalType) {
            case 'LEADING':
                return '🔮';
            case 'CONFIRMING':
                return '✅';
            case 'LAGGING':
                return '📉';
            case 'DIVERGING':
                return '⚠️';
            default:
                return '⚪';
        }
    };

    // Get signal type color
    const getSignalColor = (signalType) => {
        switch(signalType) {
            case 'LEADING':
                return '#6366F1';
            case 'CONFIRMING':
                return '#4CAF50';
            case 'LAGGING':
                return '#FF9800';
            case 'DIVERGING':
                return '#F44336';
            default:
                return '#9E9E9E';
        }
    };

    // Render single option card
    const renderOptionCard = (symbol, label, position) => {
        const vel = premiumVelocity[symbol];
        if (!vel) {
            return (
                <Box key={symbol} sx={{ flex: 1, p: 1.5, textAlign: 'center', color: 'text.secondary' }}>
                    <Typography variant="caption">{label}</Typography>
                    <Typography variant="body2">No data</Typography>
                </Box>
            );
        }

        const isPositive = vel.currentVelocity > 0;
        const velocity = vel.currentVelocity || 0;
        const pctVel = vel.pctVelocity || 0;
        const accel = vel.acceleration || 0;
        const signalType = vel.signalType || 'NEUTRAL';
        const velocityRatio = vel.velocityRatio || 0;

        const velocityColor = isPositive ? '#4CAF50' : '#F44336';
        const accelColor = accel > 0.01 ? '#4CAF50' : (accel < -0.01 ? '#F44336' : '#FFC107');
        const signalColor = getSignalColor(signalType);
        const signalEmoji = getSignalEmoji(signalType);

        let ratioDisplay = 'N/A';
        if (velocityRatio === 999.0) {
            ratioDisplay = '∞x';
        } else if (velocityRatio > 0.1) {
            ratioDisplay = `${velocityRatio.toFixed(1)}x`;
        } else if (velocityRatio === 0) {
            ratioDisplay = 'Indep';
        }

        return (
            <Box
                key={symbol}
                sx={{
                    flex: 1,
                    p: 1.5,
                    borderRadius: 1.5,
                    background: 'linear-gradient(135deg, rgba(255,255,255,0.5) 0%, rgba(248,249,250,0.5) 100%)',
                    border: `2px solid ${velocityColor}`,
                }}
            >
                {/* Header */}
                <Box sx={{ mb: 1 }}>
                    <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#333', mb: 0.3 }}>
                        {label}
                    </Typography>
                    <Chip
                        size="small"
                        label={`${signalEmoji} ${signalType}`}
                        sx={{
                            width: '100%',
                            background: signalColor,
                            color: 'white',
                            fontWeight: 'bold',
                            fontSize: '0.7rem',
                            height: 20
                        }}
                    />
                </Box>

                {/* Velocity */}
                <Box
                    sx={{
                        p: 0.8,
                        mb: 0.8,
                        borderRadius: 1,
                        background: velocityColor,
                        color: 'white',
                    }}
                >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.3, mb: 0.2 }}>
                        {isPositive ? (
                            <TrendingUpIcon sx={{ fontSize: 16 }} />
                        ) : (
                            <TrendingDownIcon sx={{ fontSize: 16 }} />
                        )}
                        <Typography variant="body2" sx={{ fontWeight: 'bold', m: 0 }}>
                            {velocity > 0 ? '+' : ''}{velocity.toFixed(3)} ₹/s
                        </Typography>
                    </Box>
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.9)' }}>
                        {pctVel > 0 ? '+' : ''}{pctVel.toFixed(2)}%/s
                    </Typography>
                </Box>

                {/* Acceleration & Ratio */}
                <Box sx={{ display: 'flex', gap: 0.5, fontSize: '0.75rem' }}>
                    <Box sx={{ flex: 1 }}>
                        <Typography variant="caption" sx={{ fontWeight: 600, color: accelColor, display: 'block' }}>
                            {accel > 0.01 ? '⬆️' : (accel < -0.01 ? '⬇️' : '↔️')} {Math.abs(accel).toFixed(4)}
                        </Typography>
                    </Box>
                    <Box sx={{ flex: 1, textAlign: 'right' }}>
                        <Typography variant="caption" sx={{ fontWeight: 600, color: signalColor, display: 'block' }}>
                            {ratioDisplay}
                        </Typography>
                    </Box>
                </Box>
            </Box>
        );
    };

    if (!ceSymbol && !peSymbol) {
        return (
            <Paper elevation={3} sx={{ p: 2, background: 'linear-gradient(145deg, #ffffff 0%, #f8f9fa 100%)', borderRadius: 2 }}>
                <Typography variant="body2" sx={{ mb: 1, fontWeight: 600, color: 'text.secondary' }}>
                    📊 Premium Velocity
                </Typography>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    Waiting for trade...
                </Typography>
            </Paper>
        );
    }

    return (
        <Paper
            elevation={3}
            sx={{
                p: 2,
                background: 'linear-gradient(145deg, #ffffff 0%, #f8f9fa 100%)',
                borderRadius: 2,
                border: '1px solid rgba(0,0,0,0.05)',
                '&:hover': {
                    boxShadow: 6,
                    transition: 'box-shadow 0.3s ease-in-out'
                }
            }}
        >
            <Typography variant="body2" sx={{ mb: 2, fontWeight: 600 }}>
                📊 Premium Velocity (CE vs PE)
            </Typography>

            {/* CE and PE Side by Side */}
            <Box sx={{ display: 'flex', gap: 1.5 }}>
                {ceSymbol && renderOptionCard(ceSymbol, '📈 Call (CE)', 'left')}
                {peSymbol && renderOptionCard(peSymbol, '📉 Put (PE)', 'right')}
            </Box>

            {/* Mini Legend */}
            <Box sx={{ mt: 1.5, pt: 1, borderTop: '1px solid rgba(0,0,0,0.1)', fontSize: '0.7rem' }}>
                <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
                    <span style={{ color: '#6366F1' }}>🔮 LEADING</span> • <span style={{ color: '#4CAF50' }}>✅ CONFIRMING</span> • <span style={{ color: '#FF9800' }}>📉 LAGGING</span>
                </Typography>
            </Box>
        </Paper>
    );
}
