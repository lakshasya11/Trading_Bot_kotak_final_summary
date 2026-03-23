import React from 'react';
import { Paper, Typography, Box, Chip } from '@mui/material';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import CalendarTodayIcon from '@mui/icons-material/CalendarToday';
import PersonIcon from '@mui/icons-material/Person';

export default function StatusPanel({ status, socketStatus, expiryInfo, activeUser }) {
    const isConnected = status.connection === 'CONNECTED';
    const modeColor = status.mode.includes("PAPER") ? 'info.main' : 'warning.main';
    const isBullish = status.trend === 'BULLISH';

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
            <Typography variant="body2" sx={{ mb: 2, fontWeight: 600 }}>Live Status</Typography>
            <Box sx={{ pl: 0 }}>
                {/* 👤 ACTIVE USER DISPLAY */}
                {activeUser && (
                    <Box sx={{ 
                        mb: 2, 
                        p: 1.5, 
                        borderRadius: 1.5,
                        background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
                        boxShadow: 2
                    }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.3 }}>
                            <PersonIcon sx={{ fontSize: 16, color: 'rgba(255,255,255,0.9)' }} />
                            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.8)' }}>
                                Logged In As
                            </Typography>
                        </Box>
                        <Typography variant="body1" sx={{ color: 'white', fontWeight: 'bold' }}>
                            {activeUser.name}
                        </Typography>
                        {activeUser.description && (
                            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.7)', display: 'block', mt: 0.3 }}>
                                {activeUser.description}
                            </Typography>
                        )}
                    </Box>
                )}

                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1.5 }}>
                    <Typography variant="body1" sx={{ fontWeight: 600 }}>Status:</Typography>
                    <Chip
                        label={status.connection}
                        color={socketStatus === 'CONNECTED' ? 'success' : 'error'}
                        size="small"
                        sx={{ fontWeight: 600 }}
                    />
                </Box>

                <Box sx={{
                    p: 1.5,
                    mb: 1.5,
                    borderRadius: 1.5,
                    background: status.mode.includes("PAPER") ?
                        'linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)' :
                        'linear-gradient(135deg, #f59e0b 0%, #d97706 100%)',
                    boxShadow: 2
                }}>
                    <Typography variant="body2" sx={{ color: 'white', fontWeight: 600, mb: 0.5 }}>
                        Trading Mode
                    </Typography>
                    <Typography variant="h6" sx={{ color: 'white', fontWeight: 'bold' }}>
                        {status.mode}
                    </Typography>
                </Box>

                <Box sx={{
                    p: 1.5,
                    mb: 1.5,
                    borderRadius: 1.5,
                    background: 'linear-gradient(135deg, #1976d2 0%, #1565c0 100%)',
                    boxShadow: 2
                }}>
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.8)', display: 'block', mb: 0.5 }}>
                        {status.indexName || 'INDEX'}
                    </Typography>
                    <Typography variant="h4" sx={{ color: 'white', fontWeight: 'bold' }}>
                        {status.indexPrice?.toFixed(2) ?? '0.00'}
                    </Typography>
                </Box>

                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Typography variant="body1" sx={{ fontWeight: 600 }}>Trend:</Typography>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                        {isBullish ?
                            <TrendingUpIcon sx={{ color: 'success.main', fontSize: 20 }} /> :
                            <TrendingDownIcon sx={{ color: 'error.main', fontSize: 20 }} />
                        }
                        <Typography
                            sx={{
                                color: isBullish ? 'success.main' : 'error.main',
                                fontWeight: 'bold',
                                fontSize: '1.1rem'
                            }}
                        >
                            {status.trend}
                        </Typography>
                    </Box>
                </Box>

                {/* 📅 EXPIRY INFORMATION */}
                {expiryInfo && (
                    <Box sx={{ mt: 2, pt: 2, borderTop: '1px solid rgba(0,0,0,0.1)' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
                            <CalendarTodayIcon sx={{ fontSize: 18, color: 'primary.main' }} />
                            <Typography variant="body2" sx={{ fontWeight: 600 }}>Option Expiry</Typography>
                        </Box>
                        <Typography variant="caption" sx={{ display: 'block', mb: 0.5, color: 'text.secondary' }}>
                            Selected: <strong>{expiryInfo.selected_expiry || expiryInfo.current_expiry || 'N/A'}</strong>
                        </Typography>
                        {expiryInfo.available_expiries && Array.isArray(expiryInfo.available_expiries) && expiryInfo.available_expiries.length > 0 && (
                            <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 1 }}>
                                {expiryInfo.available_expiries.slice(0, 4).map((date) => (
                                    <Chip
                                        key={date}
                                        label={date}
                                        size="small"
                                        variant={date === (expiryInfo.selected_expiry || expiryInfo.current_expiry) ? 'filled' : 'outlined'}
                                        color={date === (expiryInfo.selected_expiry || expiryInfo.current_expiry) ? 'primary' : 'default'}
                                        sx={{ fontSize: '0.65rem' }}
                                    />
                                ))}
                                {expiryInfo.available_expiries.length > 4 && (
                                    <Chip
                                        label={`+${expiryInfo.available_expiries.length - 4} more`}
                                        size="small"
                                        variant="outlined"
                                        sx={{ fontSize: '0.65rem' }}
                                    />
                                )}
                            </Box>
                        )}
                    </Box>
                )}
            </Box>
        </Paper>
    );
}