import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Grid,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  CircularProgress,
  Alert,
  Tooltip
} from '@mui/material';
import { getEntrySignals } from '../services/api';
// Using text-based indicators instead of @mui/icons-material to avoid dependency
const TrendUp = () => <span style={{ color: '#4caf50', fontWeight: 'bold' }}>↗</span>;
const TrendDown = () => <span style={{ color: '#f44336', fontWeight: 'bold' }}>↘</span>;
const InfoIcon = () => <span style={{ color: '#2196f3', fontWeight: 'bold' }}>ℹ</span>;
const CheckIcon = () => <span style={{ color: '#4caf50', fontWeight: 'bold' }}>✓</span>;
const CancelIcon = () => <span style={{ color: '#f44336', fontWeight: 'bold' }}>✗</span>;
const WarningIcon = () => <span style={{ color: '#ff9800', fontWeight: 'bold' }}>⚠</span>;

const EntrySignalsPanel = ({ websocket }) => {
  const [entrySignals, setEntrySignals] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const intervalRef = useRef(null);
  const lastCallRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    
    // Clear any existing interval
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
    }
    
    // Fetch initial entry signals
    fetchEntrySignals();
    
    // Set up periodic refresh with longer interval to reduce load
    intervalRef.current = setInterval(fetchEntrySignals, 8000); // Refresh every 8 seconds
    
    // Listen for real-time updates via websocket
    let handleMessage = null;
    
    if (websocket && websocket.readyState === WebSocket.OPEN) {
      handleMessage = (event) => {
        if (!mountedRef.current) return;
        
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'entry_signals_update') {
            setEntrySignals(data.payload);
            setLoading(false);
          }
        } catch (e) {
          console.warn('Failed to parse WebSocket message:', e);
        }
      };
      
      websocket.addEventListener('message', handleMessage);
    }
    
    // Cleanup function
    return () => {
      mountedRef.current = false;
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      if (websocket && handleMessage) {
        websocket.removeEventListener('message', handleMessage);
      }
    };
  }, [websocket, fetchEntrySignals]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      mountedRef.current = false;
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, []);

  const fetchEntrySignals = useCallback(async () => {
    if (!mountedRef.current) return;
    
    try {
      // Prevent rapid successive calls
      const now = Date.now();
      if (now - lastCallRef.current < 2000) {
        return;
      }
      lastCallRef.current = now;
      
      const data = await getEntrySignals();
      
      if (!mountedRef.current) return;
      
      setEntrySignals(prevData => {
        // Only update if data actually changed
        if (JSON.stringify(prevData) === JSON.stringify(data)) {
          return prevData;
        }
        return data;
      });
      setError(null);
    } catch (err) {
      if (!mountedRef.current) return;
      console.error('Entry signals fetch error:', err);
      setError('Network error: ' + err.message);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, []);

  const getStrategyColor = (strategy) => {
    const colors = {
      'Volatility Breakout': '#e53e3e',      // Red - Highest priority
      'Supertrend Flip': '#3182ce',          // Blue
      'Trend Continuation': '#38a169',       // Green  
      'Counter-Trend': '#d69e2e',            // Yellow - Lowest priority
      'No Signal': '#718096'                 // Gray
    };
    return colors[strategy] || '#718096';
  };

  const getConditionIcon = (met) => {
    if (met === true) return <CheckIcon />;
    if (met === false) return <CancelIcon />;
    return <WarningIcon />;
  };

  const formatPrice = (price) => {
    return typeof price === 'number' ? price.toFixed(2) : '--';
  };

  if (loading) {
    return (
      <Card sx={{ mt: 2 }}>
        <CardContent sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
          <CircularProgress />
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Alert severity="error">{error}</Alert>
        </CardContent>
      </Card>
    );
  }

  if (!entrySignals) {
    return (
      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Alert severity="info">No entry signals available</Alert>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card sx={{ mt: 2 }}>
      <CardContent>
        <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <InfoIcon />
          Entry Signals - V47.14 Strategy Conditions
        </Typography>

        <Grid container spacing={2}>
          {/* Current Market Status */}
          <Grid item xs={12} md={3}>
            <Paper sx={{ p: 2, textAlign: 'center' }}>
              <Typography variant="subtitle2" color="text.secondary">
                Current Index
              </Typography>
              <Typography variant="h6">
                {formatPrice(entrySignals.current_price)}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                ATM Strike: {entrySignals.atm_strike}
              </Typography>
            </Paper>
          </Grid>

          {/* Active Strategy */}
          <Grid item xs={12} md={3}>
            <Paper sx={{ p: 2, textAlign: 'center' }}>
              <Typography variant="subtitle2" color="text.secondary">
                Priority Strategy
              </Typography>
              <Chip
                label={entrySignals.active_strategy || 'No Signal'}
                sx={{ 
                  backgroundColor: getStrategyColor(entrySignals.active_strategy),
                  color: 'white',
                  fontWeight: 'bold'
                }}
              />
            </Paper>
          </Grid>

          {/* Supertrend Status */}
          <Grid item xs={12} md={3}>
            <Paper sx={{ p: 2, textAlign: 'center' }}>
              <Typography variant="subtitle2" color="text.secondary">
                Supertrend
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1 }}>
                {entrySignals.supertrend_direction === 'UP' ? (
                  <TrendUp />
                ) : (
                  <TrendDown />
                )}
                <Typography variant="body1">
                  {formatPrice(entrySignals.supertrend_value)}
                </Typography>
              </Box>
              <Typography variant="caption" color="text.secondary">
                {entrySignals.supertrend_direction}
              </Typography>
            </Paper>
          </Grid>

          {/* Entry Readiness */}
          <Grid item xs={12} md={3}>
            <Paper sx={{ p: 2, textAlign: 'center' }}>
              <Typography variant="subtitle2" color="text.secondary">
                Entry Ready
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1 }}>
                {getConditionIcon(entrySignals.entry_ready)}
                <Typography variant="body1">
                  {entrySignals.entry_ready ? 'YES' : 'NO'}
                </Typography>
              </Box>
            </Paper>
          </Grid>
        </Grid>

        {/* Potential Entry Options */}
        {entrySignals.potential_entries && entrySignals.potential_entries.length > 0 && (
          <Box sx={{ mt: 3 }}>
            <Typography variant="subtitle1" gutterBottom>
              Potential Entry Options
            </Typography>
            <TableContainer component={Paper}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Strategy</TableCell>
                    <TableCell>Side</TableCell>
                    <TableCell>Strike</TableCell>
                    <TableCell>Option Price</TableCell>
                    <TableCell>Reason</TableCell>
                    <TableCell>Conditions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {entrySignals.potential_entries.map((entry, index) => (
                    <TableRow key={index}>
                      <TableCell>
                        <Chip 
                          label={entry.strategy}
                          size="small"
                          sx={{ 
                            backgroundColor: getStrategyColor(entry.strategy),
                            color: 'white'
                          }}
                        />
                      </TableCell>
                      <TableCell>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          {entry.side === 'CALL' ? (
                            <TrendUp />
                          ) : (
                            <TrendDown />
                          )}
                          {entry.side}
                        </Box>
                      </TableCell>
                      <TableCell>{entry.strike}</TableCell>
                      <TableCell>{formatPrice(entry.option_price)}</TableCell>
                      <TableCell>
                        <Tooltip title={entry.reason}>
                          <Typography variant="body2" noWrap sx={{ maxWidth: 150 }}>
                            {entry.reason}
                          </Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell>
                        <Box sx={{ display: 'flex', gap: 0.5 }}>
                          {entry.conditions && Object.entries(entry.conditions).map(([key, met]) => (
                            <Tooltip key={key} title={`${key}: ${met ? 'Met' : 'Not Met'}`}>
                              {getConditionIcon(met)}
                            </Tooltip>
                          ))}
                        </Box>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        )}

        {/* Strategy Conditions Overview */}
        {entrySignals.strategy_conditions && (
          <Box sx={{ mt: 3 }}>
            <Typography variant="subtitle1" gutterBottom>
              V47.14 Strategy Conditions Status
            </Typography>
            <Grid container spacing={2}>
              {Object.entries(entrySignals.strategy_conditions).map(([strategy, conditions]) => (
                <Grid item xs={12} md={6} lg={3} key={strategy}>
                  <Paper sx={{ p: 2 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      {strategy}
                    </Typography>
                    {Object.entries(conditions).map(([condition, met]) => (
                      <Box key={condition} sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                        {getConditionIcon(met)}
                        <Typography variant="body2">
                          {condition.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                        </Typography>
                      </Box>
                    ))}
                  </Paper>
                </Grid>
              ))}
            </Grid>
          </Box>
        )}

        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>
          Last updated: {new Date(entrySignals.timestamp || Date.now()).toLocaleTimeString()}
        </Typography>
      </CardContent>
    </Card>
  );
};

export default React.memo(EntrySignalsPanel);