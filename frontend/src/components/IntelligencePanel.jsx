import React, { useState } from 'react';
import { Paper, Typography, Button, CircularProgress } from '@mui/material';
import { useSnackbar } from 'notistack';
import { runOptimizer, resetParams, resetUoaWatchlist } from '../services/api';
import { useStore } from '../store/store'; // Add this import

export default function IntelligencePanel() {
    const [loading, setLoading] = useState(false);
    const [resetLoading, setResetLoading] = useState(false);
    const [clearUoaLoading, setClearUoaLoading] = useState(false);
    const isSpectator = useStore(state => state.isSpectatorMode); // Get the flag
    const { enqueueSnackbar } = useSnackbar();

    const handleOptimize = async () => {
        setLoading(true);
        enqueueSnackbar('Starting optimization... This may take a moment.', { variant: 'info' });
        try {
            const data = await runOptimizer();
            enqueueSnackbar(data.report.join(' | '), { 
                variant: 'success', 
                style: { whiteSpace: 'pre-line' },
                autoHideDuration: 8000
            });
        } catch (error) {
            enqueueSnackbar(error.message, { variant: 'error', style: { whiteSpace: 'pre-line' } });
        }
        setLoading(false);
    };

    const handleReset = async () => {
        if (window.confirm('This will reset your strategy parameters to market standard defaults. Are you sure?')) {
            setResetLoading(true);
            try {
                const data = await resetParams();
                enqueueSnackbar(data.message, { variant: 'success' });
            } catch (error) {
                enqueueSnackbar(error.message, { variant: 'error' });
            }
            setResetLoading(false);
        }
    };

    const handleClearUoa = async () => {
        if (window.confirm('Are you sure you want to clear the entire UOA Watchlist?')) {
            setClearUoaLoading(true);
            try {
                const data = await resetUoaWatchlist();
                enqueueSnackbar(data.message, { variant: 'success' });
            } catch (error) {
                enqueueSnackbar(error.message, { variant: 'error' });
            }
            setClearUoaLoading(false);
        }
    };

    return (
        <Paper elevation={3} sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 1 }}>Intelligence</Typography>
            <Button fullWidth variant="outlined" sx={{ mb: 1 }} onClick={handleOptimize} disabled={loading || resetLoading || clearUoaLoading || isSpectator}>
                {loading ? <CircularProgress size={24} /> : 'Analyze & Optimize Now'}
            </Button>
            <Button fullWidth variant="outlined" color="warning" sx={{ mb: 1 }} onClick={handleReset} disabled={loading || resetLoading || clearUoaLoading || isSpectator}>
                {resetLoading ? <CircularProgress size={24} color="inherit" /> : 'Reset to Market Standards'}
            </Button>
            <Button fullWidth variant="outlined" color="error" onClick={handleClearUoa} disabled={loading || resetLoading || clearUoaLoading || isSpectator}>
                {clearUoaLoading ? <CircularProgress size={24} color="inherit" /> : 'Clear UOA Watchlist'}
            </Button>
        </Paper>
    );
}