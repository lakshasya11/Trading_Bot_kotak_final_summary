import React from 'react';
import { Paper, Typography, Box, Chip } from '@mui/material';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import TrendingFlatIcon from '@mui/icons-material/TrendingFlat';
import AccessTimeIcon from '@mui/icons-material/AccessTime';
import ShowChartIcon from '@mui/icons-material/ShowChart';

export default function SupertrendAnglePanel({ stAngleData }) {
    if (!stAngleData || !stAngleData.symbol) {
        return (
            <Paper
                elevation={3}
                sx={{
                    p: 2,
                    background: 'linear-gradient(145deg, #ffffff 0%, #f8f9fa 100%)',
                    borderRadius: 2,
                    border: '1px solid rgba(0,0,0,0.05)',
                }}
            >
                <Typography variant="body2" sx={{ mb: 2, fontWeight: 600 }}>
                    📐 Supertrend Angle Monitor
                </Typography>
                <Box sx={{ textAlign: 'center', py: 2, opacity: 0.5 }}>
                    <Typography variant="body2">
                        Waiting for ATM option data...
                    </Typography>
                </Box>
            </Paper>
        );
    }

    const { symbol, st_line, current_price, angle, acceleration, status, increase_duration_str } = stAngleData;

    // Determine status color and icon
    let statusColor = 'default';
    let StatusIcon = TrendingFlatIcon;
    let statusBg = 'linear-gradient(135deg, #9e9e9e 0%, #757575 100%)';

    if (status === 'increasing') {
        statusColor = 'success';
        StatusIcon = TrendingUpIcon;
        statusBg = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';
    } else if (status === 'decreasing') {
        statusColor = 'error';
        StatusIcon = TrendingDownIcon;
        statusBg = 'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)';
    }

    // Determine angle color based on value
    let angleColor = '#666';
    if (angle !== null) {
        if (angle > 0.7) {
            angleColor = '#10b981'; // Strong increasing - green
        } else if (angle > 0.25) {
            angleColor = '#3b82f6'; // Moderate increasing - blue
        } else if (angle < -0.1) {
            angleColor = '#ef4444'; // Decreasing - red
        }
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
                📐 Supertrend Angle Monitor
            </Typography>

            {/* Option Symbol and Prices */}
            <Box sx={{
                p: 1.5,
                mb: 1.5,
                borderRadius: 1.5,
                background: 'linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)',
                boxShadow: 2
            }}>
                <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.8)', display: 'block', mb: 0.5 }}>
                    ATM OPTION
                </Typography>
                <Typography variant="body1" sx={{ color: 'white', fontWeight: 'bold', mb: 1 }}>
                    {symbol}
                </Typography>
                
                {/* Current Price */}
                <Box sx={{ 
                    display: 'flex', 
                    alignItems: 'center', 
                    justifyContent: 'space-between',
                    mb: 0.7,
                    pb: 0.7,
                    borderBottom: '1px solid rgba(255,255,255,0.3)'
                }}>
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.9)' }}>
                        CURRENT PRICE
                    </Typography>
                    <Typography variant="h6" sx={{ color: '#fbbf24', fontWeight: 'bold' }}>
                        ₹{current_price !== null ? current_price.toFixed(2) : '---'}
                    </Typography>
                </Box>
                
                {/* ST Line */}
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.9)' }}>
                        ST LINE (Support/Resistance)
                    </Typography>
                    <Typography variant="h6" sx={{ color: '#60a5fa', fontWeight: 'bold' }}>
                        ₹{st_line !== null ? st_line.toFixed(2) : '---'}
                    </Typography>
                </Box>
            </Box>

            {/* ST Angle Value */}
            <Box sx={{
                p: 1.5,
                mb: 1.5,
                borderRadius: 1.5,
                background: 'linear-gradient(135deg, #f3f4f6 0%, #e5e7eb 100%)',
                border: '2px solid rgba(0,0,0,0.1)',
            }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 0.5 }}>
                    <Typography variant="caption" sx={{ color: '#666', fontWeight: 600 }}>
                        ST ANGLE
                    </Typography>
                    <ShowChartIcon sx={{ color: angleColor, fontSize: 18 }} />
                </Box>
                <Typography variant="h4" sx={{ color: angleColor, fontWeight: 'bold' }}>
                    {angle !== null ? `${angle > 0 ? '+' : ''}${angle.toFixed(2)}%` : '---'}
                </Typography>
                <Typography variant="caption" sx={{ color: '#999' }}>
                    per candle
                </Typography>
            </Box>

            {/* Status and Duration */}
            <Box sx={{
                p: 1.5,
                mb: 1.5,
                borderRadius: 1.5,
                background: statusBg,
                boxShadow: 2
            }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 0.5 }}>
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.9)', fontWeight: 600 }}>
                        STATUS
                    </Typography>
                    <StatusIcon sx={{ color: 'white', fontSize: 20 }} />
                </Box>
                <Typography variant="h6" sx={{ color: 'white', fontWeight: 'bold', textTransform: 'uppercase' }}>
                    {status}
                </Typography>
                
                {status === 'increasing' && (
                    <Box sx={{ mt: 1, pt: 1, borderTop: '1px solid rgba(255,255,255,0.3)' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                            <AccessTimeIcon sx={{ color: 'rgba(255,255,255,0.9)', fontSize: 16 }} />
                            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.9)' }}>
                                Increasing for:
                            </Typography>
                        </Box>
                        <Typography variant="h6" sx={{ color: 'white', fontWeight: 'bold', mt: 0.5 }}>
                            {increase_duration_str}
                        </Typography>
                    </Box>
                )}
            </Box>

            {/* Acceleration */}
            {acceleration !== null && (
                <Box sx={{
                    p: 1,
                    borderRadius: 1,
                    background: 'rgba(0,0,0,0.02)',
                    border: '1px solid rgba(0,0,0,0.05)'
                }}>
                    <Typography variant="caption" sx={{ color: '#666', display: 'block', mb: 0.3 }}>
                        ACCELERATION
                    </Typography>
                    <Typography variant="body1" sx={{ 
                        fontWeight: 'bold', 
                        color: acceleration > 0 ? '#10b981' : acceleration < 0 ? '#ef4444' : '#666'
                    }}>
                        {acceleration > 0 ? '+' : ''}{acceleration.toFixed(2)}%
                    </Typography>
                </Box>
            )}
        </Paper>
    );
}
