import React from 'react';
import { Paper, Typography, Grid, Box } from '@mui/material';
import { useStore } from '../store/store';

// A small, reusable component for consistent text styling
const StatDisplay = ({ title, value, color = 'text.primary', unit = '' }) => (
    <Box sx={{ textAlign: 'center' }}>
        <Typography variant="caption" color="text.secondary" display="block">
            {title}
        </Typography>
        <Typography variant="h6" sx={{ color, fontWeight: 'bold' }}>
            {value}
            <Typography component="span" variant="body2" sx={{ ml: 0.5 }}>{unit}</Typography>
        </Typography>
    </Box>
);

export default function StraddleMonitor() {
    // Subscribe to the new straddleData state from the store
    const data = useStore(state => state.straddleData);

    if (!data) {
        return null; // Don't render anything if data hasn't arrived
    }

    const changeColor = data.change_pct > 0 ? 'success.main' : data.change_pct < 0 ? 'error.main' : 'text.primary';

    return (
        <Paper elevation={3} sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 2, textAlign: 'left' }}>ATM Straddle Monitor</Typography>
            <Grid container spacing={2} justifyContent="space-around">
                <Grid item xs={4}>
                    <StatDisplay
                        title="Current Value"
                        value={data.current_straddle.toFixed(2)}
                        unit="₹"
                    />
                </Grid>
                <Grid item xs={4}>
                    <StatDisplay
                        title="Open Value"
                        value={data.open_straddle.toFixed(2)}
                        unit="₹"
                    />
                </Grid>
                <Grid item xs={4}>
                    <StatDisplay
                        title="Day's Change"
                        value={data.change_pct.toFixed(2)}
                        unit="%"
                        color={changeColor}
                    />
                </Grid>
            </Grid>
        </Paper>
    );
}
