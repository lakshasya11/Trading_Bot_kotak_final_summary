import React from 'react';
import { Paper, Typography, Box, Grid } from '@mui/material';

// A small, reusable component for consistent text styling within the panel
const PnlText = ({ title, value, color = 'text.primary', isBold = false }) => {
    // Handle null, undefined, NaN, or invalid values
    const safeValue = (value == null || isNaN(value)) ? 0 : Number(value);
    return (
        <Typography variant="body1" sx={{ color, fontWeight: isBold ? 'bold' : 'normal' }}>
            {title}: <Typography component="span" sx={{ fontWeight: 'bold' }}>₹ {safeValue.toFixed(2)}</Typography>
        </Typography>
    );
};

export default function NetPerformancePanel({ data }) {
    // Handle null/undefined data object
    if (!data) {
        data = { grossPnl: 0, totalCharges: 0, netPnl: 0, wins: 0, losses: 0 };
    }
    
    // Safely extract values with defaults
    const grossPnl = (data.grossPnl == null || isNaN(data.grossPnl)) ? 0 : Number(data.grossPnl);
    const totalCharges = (data.totalCharges == null || isNaN(data.totalCharges)) ? 0 : Number(data.totalCharges);
    const netPnl = (data.netPnl == null || isNaN(data.netPnl)) ? 0 : Number(data.netPnl);
    const wins = (data.wins == null || isNaN(data.wins)) ? 0 : Number(data.wins);
    const losses = (data.losses == null || isNaN(data.losses)) ? 0 : Number(data.losses);
    
    // Determine the color for P&L values based on whether they are positive or negative
    const netPnlColor = netPnl > 0 ? 'success.main' : netPnl < 0 ? 'error.main' : 'text.primary';
    const grossPnlColor = grossPnl > 0 ? 'success.main' : grossPnl < 0 ? 'error.main' : 'text.primary';
    
    return (
        <Paper elevation={3} sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 1 }}>Daily Performance</Typography>
            <Grid container spacing={0.5} sx={{ pl: 1 }}>
                <Grid item xs={12}>
                    <PnlText title="Gross P&L" value={grossPnl} color={grossPnlColor} />
                </Grid>
                <Grid item xs={12}>
                    <PnlText title="(-) Total Charges" value={totalCharges} color="text.secondary" />
                </Grid>
                <Grid item xs={12} sx={{ borderTop: '1px solid #e0e0e0', pt: 0.5, mt: 0.5 }}>
                    <PnlText title="Net P&L" value={netPnl} color={netPnlColor} isBold={true} />
                </Grid>
                <Grid item xs={12} sx={{ mt: 1 }}>
                     <Typography variant="body2">Wins: {wins} | Losses: {losses}</Typography>
                </Grid>
            </Grid>
        </Paper>
    );
}

