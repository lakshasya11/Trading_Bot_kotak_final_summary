import React from 'react';
import { Paper, Typography, Box, Chip, Grid } from '@mui/material';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CancelIcon from '@mui/icons-material/Cancel';
import AccessTimeIcon from '@mui/icons-material/AccessTime';

export default function TrendDirectionScoutPanel({ trendData }) {
    if (!trendData || !trendData.atm_strike) {
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
                    📊 Trend Direction Scout
                </Typography>
                <Box sx={{ textAlign: 'center', py: 2, opacity: 0.5 }}>
                    <Typography variant="body2">
                        Analyzing trend direction...
                    </Typography>
                </Box>
            </Paper>
        );
    }

    const { atm_strike, ce_option, pe_option, recommendation, confidence, overall_trend } = trendData;

    // Check if we have valid supertrend data
    const hasValidData = ce_option?.st_line !== null && pe_option?.st_line !== null;

    // If no valid data yet, show loading message
    if (!hasValidData) {
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
                    📊 Trend Direction Scout
                </Typography>
                <Box sx={{ textAlign: 'center', py: 3, opacity: 0.6 }}>
                    <Typography variant="body2" sx={{ mb: 1 }}>
                        Collecting candle data...
                    </Typography>
                    <Typography variant="caption" sx={{ color: '#999' }}>
                        Waiting for ATM strike {atm_strike} options to build supertrend history
                    </Typography>
                </Box>
            </Paper>
        );
    }

    // Determine recommendation color and icon
    const getRecommendationColor = (rec) => {
        if (rec === 'CE_STRONG') return { bg: '#10b98166', text: '#059669', icon: TrendingUpIcon };
        if (rec === 'PE_STRONG') return { bg: '#ef444466', text: '#dc2626', icon: TrendingDownIcon };
        if (rec === 'CE_WEAK') return { bg: '#3b82f666', text: '#1d4ed8', icon: TrendingUpIcon };
        if (rec === 'PE_WEAK') return { bg: '#f97316aa', text: '#ea580c', icon: TrendingDownIcon };
        return { bg: '#9ca3af66', text: '#4b5563', icon: null };
    };

    const recColor = getRecommendationColor(recommendation);
    const RecIcon = recColor.icon;

    const OptionCard = ({ option, type, isRecommended }) => {
        if (!option || option.st_line === null) return null;
        
        const isGreen = option.is_green;
        const cardBg = isGreen 
            ? 'linear-gradient(135deg, rgba(16, 185, 129, 0.1) 0%, rgba(5, 150, 105, 0.1) 100%)'
            : 'linear-gradient(135deg, rgba(239, 68, 68, 0.1) 0%, rgba(220, 38, 38, 0.1) 100%)';
        
        const statusColor = isGreen ? '#10b981' : '#ef4444';
        const statusLabel = isGreen ? 'GREEN' : 'RED';
        const statusBg = isGreen ? '#dcfce7' : '#fee2e2';
        
        return (
            <Paper
                sx={{
                    p: 2,
                    background: cardBg,
                    border: `2px solid ${statusColor}`,
                    borderRadius: 2,
                    position: 'relative',
                    opacity: isRecommended ? 1 : 0.85,
                    transform: isRecommended ? 'scale(1.02)' : 'scale(1)',
                    transition: 'all 0.3s ease'
                }}
            >
                {isRecommended && (
                    <Box sx={{ 
                        position: 'absolute',
                        top: -12,
                        right: 12,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 0.5,
                        backgroundColor: '#fbbf24',
                        padding: '4px 12px',
                        borderRadius: 20,
                        boxShadow: 2
                    }}>
                        <CheckCircleIcon sx={{ fontSize: 16, color: '#92400e' }} />
                        <Typography variant="caption" sx={{ fontWeight: 'bold', color: '#92400e' }}>
                            RECOMMENDED
                        </Typography>
                    </Box>
                )}

                {/* Header */}
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1.5 }}>
                    <Typography variant="h6" sx={{ fontWeight: 'bold' }}>
                        {atm_strike} {type}
                    </Typography>
                    <Chip
                        label={statusLabel}
                        sx={{
                            backgroundColor: statusBg,
                            color: statusColor,
                            fontWeight: 'bold'
                        }}
                    />
                </Box>

                {/* Supertrend Status */}
                <Box sx={{ mb: 1.5, p: 1, backgroundColor: 'rgba(255,255,255,0.5)', borderRadius: 1 }}>
                    <Typography variant="caption" sx={{ color: '#666', display: 'block', fontWeight: 600 }}>
                        SUPERTREND
                    </Typography>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.5 }}>
                        <Typography variant="body2">
                            <strong>Line:</strong> ₹{option.st_line.toFixed(2)}
                        </Typography>
                        <Typography variant="body2">
                            <strong>LTP:</strong> ₹{option.ltp.toFixed(2)}
                        </Typography>
                    </Box>
                    <Typography variant="caption" sx={{ color: '#999', display: 'block', mt: 0.5 }}>
                        {option.distance_to_break > 0 
                            ? `↑ ${Math.abs(option.distance_to_break).toFixed(2)} pts above ST (Strong)`
                            : `↓ ${Math.abs(option.distance_to_break).toFixed(2)} pts below ST (Weak)`
                        }
                    </Typography>
                </Box>

                {/* Angle */}
                <Box sx={{ mb: 1.5, p: 1, backgroundColor: 'rgba(255,255,255,0.5)', borderRadius: 1 }}>
                    <Typography variant="caption" sx={{ color: '#666', display: 'block', fontWeight: 600 }}>
                        ST ANGLE & MOMENTUM
                    </Typography>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mt: 0.5 }}>
                        <Typography variant="h6" sx={{ 
                            fontWeight: 'bold',
                            color: option.st_angle > 0.7 ? '#10b981' : option.st_angle > 0.2 ? '#3b82f6' : '#ef4444'
                        }}>
                            {option.st_angle > 0 ? '+' : ''}{option.st_angle.toFixed(2)}%
                        </Typography>
                        <Box sx={{ display: 'flex', gap: 0.5 }}>
                            <Typography variant="caption" sx={{ color: '#666' }}>
                                per candle
                            </Typography>
                            {option.angle_status === 'increasing' ? (
                                <TrendingUpIcon sx={{ color: '#10b981', fontSize: 18 }} />
                            ) : option.angle_status === 'decreasing' ? (
                                <TrendingDownIcon sx={{ color: '#ef4444', fontSize: 18 }} />
                            ) : null}
                        </Box>
                    </Box>
                </Box>

                {/* Trend Duration & Strength */}
                <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1 }}>
                    <Box sx={{ p: 1, backgroundColor: 'rgba(255,255,255,0.5)', borderRadius: 1 }}>
                        <Typography variant="caption" sx={{ color: '#666', display: 'block', fontWeight: 600 }}>
                            TREND DURATION
                        </Typography>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.5 }}>
                            <AccessTimeIcon sx={{ fontSize: 16, color: '#666' }} />
                            <Typography variant="body2" sx={{ fontWeight: 'bold' }}>
                                {option.trend_duration_seconds}s
                            </Typography>
                        </Box>
                    </Box>

                    <Box sx={{ p: 1, backgroundColor: 'rgba(255,255,255,0.5)', borderRadius: 1 }}>
                        <Typography variant="caption" sx={{ color: '#666', display: 'block', fontWeight: 600 }}>
                            STRONG CANDLES
                        </Typography>
                        <Typography variant="body2" sx={{ fontWeight: 'bold', mt: 0.5, color: statusColor }}>
                            {option.candles_in_trend} consecutive
                        </Typography>
                    </Box>
                </Box>
            </Paper>
        );
    };

    return (
        <Paper
            elevation={3}
            sx={{
                p: 2.5,
                background: 'linear-gradient(145deg, #ffffff 0%, #f8f9fa 100%)',
                borderRadius: 2,
                border: '1px solid rgba(0,0,0,0.05)',
            }}
        >
            <Typography variant="body2" sx={{ mb: 1, fontWeight: 600 }}>
                📊 Trend Direction Scout
            </Typography>

            {/* Overall Trend Status */}
            <Box sx={{
                p: 2,
                mb: 2,
                borderRadius: 2,
                background: overall_trend === 'UPTREND' 
                    ? 'linear-gradient(135deg, #10b981 0%, #059669 100%)'
                    : 'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)',
                color: 'white',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                boxShadow: 3
            }}>
                <Box>
                    <Typography variant="caption" sx={{ opacity: 0.9 }}>
                        OVERALL TREND
                    </Typography>
                    <Typography variant="h5" sx={{ fontWeight: 'bold' }}>
                        {overall_trend}
                    </Typography>
                </Box>
                {overall_trend === 'UPTREND' ? (
                    <TrendingUpIcon sx={{ fontSize: 40, opacity: 0.7 }} />
                ) : (
                    <TrendingDownIcon sx={{ fontSize: 40, opacity: 0.7 }} />
                )}
            </Box>

            {/* CE and PE Options Grid */}
            <Grid container spacing={2} sx={{ mb: 2 }}>
                <Grid item xs={12} sm={6}>
                    <OptionCard 
                        option={ce_option} 
                        type="CE (Call)"
                        isRecommended={recommendation?.includes('CE')}
                    />
                </Grid>
                <Grid item xs={12} sm={6}>
                    <OptionCard 
                        option={pe_option} 
                        type="PE (Put)"
                        isRecommended={recommendation?.includes('PE')}
                    />
                </Grid>
            </Grid>

            {/* Recommendation Box */}
            <Box sx={{
                p: 2,
                borderRadius: 2,
                backgroundColor: recColor.bg,
                borderLeft: `4px solid ${recColor.text}`,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center'
            }}>
                <Box>
                    <Typography variant="caption" sx={{ color: '#666', fontWeight: 600, display: 'block' }}>
                        ENTRY RECOMMENDATION
                    </Typography>
                    <Typography variant="body1" sx={{ 
                        fontWeight: 'bold', 
                        color: recColor.text,
                        textTransform: 'uppercase',
                        mt: 0.5
                    }}>
                        {recommendation}
                    </Typography>
                </Box>

                <Box sx={{ textAlign: 'right' }}>
                    <Typography variant="caption" sx={{ color: '#666', display: 'block' }}>
                        Confidence
                    </Typography>
                    <Typography variant="h6" sx={{ 
                        fontWeight: 'bold',
                        color: confidence > 0.85 ? '#10b981' : confidence > 0.7 ? '#3b82f6' : '#ef4444',
                        mt: 0.3
                    }}>
                        {(confidence * 100).toFixed(0)}%
                    </Typography>
                </Box>
            </Box>
        </Paper>
    );
}
