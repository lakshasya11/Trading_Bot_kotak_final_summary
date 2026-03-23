import React, { useEffect, useRef, useMemo } from 'react';
import { Paper, Typography, Box, Grid, Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material';
import { createChart, ColorType } from 'lightweight-charts';
import { useStore } from '../store/store';

const ChartComponent = React.memo(({ data }) => {
    const chartContainerRef = useRef();
    const chartRef = useRef();
    const seriesRef = useRef();

    useEffect(() => {
        if (!chartContainerRef.current) return;

        if (data.length < 2) {
            // Clean up existing chart if data is insufficient
            if (chartRef.current) {
                chartRef.current.remove();
                chartRef.current = null;
                seriesRef.current = null;
            }
            return;
        }

        // Create chart only once
        if (!chartRef.current) {
            chartRef.current = createChart(chartContainerRef.current, {
                width: chartContainerRef.current.clientWidth,
                height: 200,
                layout: { textColor: '#333', background: { type: ColorType.Solid, color: 'white' } },
                grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
                timeScale: { timeVisible: true, secondsVisible: false },
            });

            seriesRef.current = chartRef.current.addAreaSeries({
                lineColor: '#2962FF', topColor: 'rgba(41, 98, 255, 0.4)', bottomColor: 'rgba(41, 98, 255, 0)',
            });

            const handleResize = () => {
                if (chartRef.current && chartContainerRef.current) {
                    chartRef.current.resize(chartContainerRef.current.clientWidth, 200);
                }
            };
            window.addEventListener('resize', handleResize);

            // Store cleanup function
            chartRef.current._cleanup = () => {
                window.removeEventListener('resize', handleResize);
            };
        }

        // Update data on existing series
        if (seriesRef.current) {
            seriesRef.current.setData(data);
            chartRef.current.timeScale().fitContent();
        }

        return () => {
            if (chartRef.current) {
                if (chartRef.current._cleanup) {
                    chartRef.current._cleanup();
                }
                chartRef.current.remove();
                chartRef.current = null;
                seriesRef.current = null;
            }
        };
    }, [data]);

    return <div ref={chartContainerRef} style={{ width: '100%', height: '200px' }} />;
}, (prevProps, nextProps) => {
    // Only re-render if data length or last value changed
    if (prevProps.data.length !== nextProps.data.length) return false;
    if (prevProps.data.length === 0) return true;
    const prevLast = prevProps.data[prevProps.data.length - 1];
    const nextLast = nextProps.data[nextProps.data.length - 1];
    return prevLast?.time === nextLast?.time && prevLast?.value === nextLast?.value;
});

export default function AnalyticsPanel({ scope = 'all', viewType = 'trades' }) {
    // Use a single selector to ensure proper re-renders when either array changes
    const tradesToAnalyze = useStore(state => {
        const trades = scope === 'today' ? state.tradeHistory : state.allTimeTradeHistory;
        return Array.isArray(trades) ? trades : [];
    });

    // ✅ DEBUG: Log trade data at render time
    console.log(`📊 AnalyticsPanel RENDER:`, {
        scope,
        tradesToAnalyzeLength: tradesToAnalyze?.length || 0,
        tradesToAnalyze: tradesToAnalyze,
        isArray: Array.isArray(tradesToAnalyze)
    });

    // CRITICAL FIX: Call all hooks BEFORE any conditional returns
    // Memoize based on trade count and last trade to avoid unnecessary recalculations
    const tradeHash = useMemo(() => {
        if (!tradesToAnalyze || tradesToAnalyze.length === 0) return '0';
        const lastTrade = tradesToAnalyze[tradesToAnalyze.length - 1];
        return `${tradesToAnalyze.length}-${lastTrade?.timestamp || ''}-${lastTrade?.net_pnl || ''}`;
    }, [tradesToAnalyze]);

    const stats = useMemo(() => {
        if (!tradesToAnalyze || tradesToAnalyze.length === 0) {
            return null;
        }

        let totalPnl = 0, grossProfit = 0, grossLoss = 0, winningTrades = 0, losingTrades = 0, maxLoss = 0;
        const equityCurve = [];
        const dailyStats = {}; // NEW: Store date-wise statistics

        try {
            const sortedTrades = [...tradesToAnalyze].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

            let lastTimestamp = 0;
            sortedTrades.forEach((trade, index) => {
                // --- THIS IS THE NEW VALIDATION BLOCK ---
                // It checks if net_pnl is a valid number before using it.
                if (typeof trade.net_pnl !== 'number' || isNaN(trade.net_pnl)) {
                    console.warn('Skipping invalid trade record for equity curve:', trade);
                    return; // Skip this data point and continue to the next one
                }
                // --- END OF NEW VALIDATION BLOCK ---

                totalPnl += trade.net_pnl;
                if (trade.pnl > 0) {
                    winningTrades++;
                    grossProfit += trade.pnl;
                } else {
                    losingTrades++;
                    grossLoss += Math.abs(trade.pnl);
                    maxLoss = Math.max(maxLoss, Math.abs(trade.pnl));
                }

                // NEW: Calculate date-wise statistics
                // FIX: Parse timestamp as IST (backend sends IST timestamps without timezone info)
                const tradeDate = new Date(trade.timestamp + '+05:30').toLocaleDateString('en-GB'); // DD/MM/YYYY format
                if (!dailyStats[tradeDate]) {
                    dailyStats[tradeDate] = {
                        date: tradeDate,
                        netPnl: 0,
                        grossProfit: 0,
                        grossLoss: 0,
                        totalTrades: 0,
                        winningTrades: 0,
                        losingTrades: 0,
                        biggestLoss: 0
                    };
                }

                const dayStats = dailyStats[tradeDate];
                dayStats.netPnl += trade.net_pnl;
                dayStats.totalTrades += 1;

                if (trade.pnl > 0) {
                    dayStats.winningTrades += 1;
                    dayStats.grossProfit += trade.pnl;
                } else {
                    dayStats.losingTrades += 1;
                    dayStats.grossLoss += Math.abs(trade.pnl);
                    dayStats.biggestLoss = Math.max(dayStats.biggestLoss, Math.abs(trade.pnl));
                }

                // Fix duplicate timestamps by adding 1 second for each duplicate
                // FIX: Parse timestamp as IST (add +05:30 timezone offset)
                let unixTime = Math.floor(new Date(trade.timestamp + '+05:30').getTime() / 1000);
                if (unixTime <= lastTimestamp) {
                    unixTime = lastTimestamp + 1;
                }
                lastTimestamp = unixTime;

                equityCurve.push({ time: unixTime, value: totalPnl });
            });

            const totalTrades = tradesToAnalyze.length;
            const winRate = totalTrades > 0 ? (winningTrades / totalTrades) * 100 : 0;
            const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : Infinity;

            // Convert dailyStats object to array and calculate profit factors
            const dailyStatsArray = Object.values(dailyStats).map(day => ({
                ...day,
                winRate: day.totalTrades > 0 ? (day.winningTrades / day.totalTrades) * 100 : 0,
                profitFactor: day.grossLoss > 0 ? day.grossProfit / day.grossLoss : (day.grossProfit > 0 ? Infinity : 0)
            })).reverse(); // Most recent first

            return {
                trades: sortedTrades.reverse(),
                equityCurve,
                summary: { totalPnl, profitFactor, totalTrades, winRate, maxLoss },
                dailyStats: dailyStatsArray, // NEW: Add daily stats to return value
            };
        } catch (error) {
            console.error('Error calculating trade statistics:', error);
            return null;
        }
    }, [tradeHash]); // Use tradeHash instead of entire array for better performance

    // CRITICAL FIX: Conditional returns AFTER all hooks are called
    // Add safety check for undefined or null
    if (!tradesToAnalyze || !Array.isArray(tradesToAnalyze)) {
        console.warn(`⚠️ AnalyticsPanel: Invalid data for scope "${scope}"`, tradesToAnalyze);
        return (
            <Box sx={{ p: 3, textAlign: 'center' }}>
                <Typography variant="body1" color="text.secondary">
                    Loading trade data...
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                    If this persists, try refreshing the page or restarting the bot.
                </Typography>
            </Box>
        );
    }

    if (tradesToAnalyze.length === 0) {
        return (
            <Box sx={{ p: 3, textAlign: 'center' }}>
                <Typography variant="body1" color="text.secondary">
                    {scope === 'today' ? 'No trades today yet.' : 'No historical trades found.'}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                    {scope === 'today'
                        ? 'Start the bot to begin trading and see results here.'
                        : 'Trade history will appear here after completing trades.'}
                </Typography>
            </Box>
        );
    }

    console.log(`📊 AnalyticsPanel rendering: ${tradesToAnalyze.length} trades for scope "${scope}", viewType "${viewType}"`);

    if (!stats) return <Typography sx={{ p: 2 }}>No trade data found for this period.</Typography>;

    const { summary, trades, equityCurve, dailyStats } = stats;

    const StatBox = React.memo(({ title, value }) => (
        <Grid item xs={6} sm={4} md={2.4}>
            <Paper sx={{ p: 1, textAlign: 'center' }}>
                <Typography variant="caption" display="block">{title}</Typography>
                <Typography variant="h6">{value}</Typography>
            </Paper>
        </Grid>
    ));

    return (
        <Box>
            <Grid container spacing={2} sx={{ mb: 2 }}>
                <StatBox title="Total Net P&L" value={`₹${summary.totalPnl.toFixed(2)}`} />
                <StatBox title="Profit Factor" value={summary.profitFactor.toFixed(2)} />
                <StatBox title="Total Trades" value={summary.totalTrades} />
                <StatBox title="Win Rate" value={`${summary.winRate.toFixed(1)}%`} />
                <StatBox title="Biggest Loss" value={`₹${summary.maxLoss.toFixed(2)}`} />
            </Grid>
            <Paper sx={{ p: 2, mb: 2 }}>
                <Typography variant="body2" sx={{ mb: 1 }}>Equity Curve</Typography>
                <ChartComponent data={equityCurve} />
            </Paper>
            <TableContainer component={Paper} sx={{ maxHeight: 350 }}>
                <Table stickyHeader size="small">
                    {viewType === 'daily' ? (
                        // Date-wise statistics view
                        <>
                            <TableHead>
                                <TableRow>
                                    <TableCell sx={{ fontWeight: 'bold' }}>Date</TableCell>
                                    <TableCell align="right" sx={{ fontWeight: 'bold' }}>Net P&L</TableCell>
                                    <TableCell align="right" sx={{ fontWeight: 'bold' }}>Profit Factor</TableCell>
                                    <TableCell align="right" sx={{ fontWeight: 'bold' }}>Total Trades</TableCell>
                                    <TableCell align="right" sx={{ fontWeight: 'bold' }}>Win %</TableCell>
                                    <TableCell align="right" sx={{ fontWeight: 'bold' }}>Biggest Loss</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {dailyStats.map((day, index) => (
                                    <TableRow key={day.date} sx={{ '&:hover': { bgcolor: 'action.hover' } }}>
                                        <TableCell sx={{ fontWeight: 'medium' }}>{day.date}</TableCell>
                                        <TableCell
                                            align="right"
                                            sx={{
                                                color: day.netPnl > 0 ? 'success.main' : 'error.main',
                                                fontWeight: 'bold'
                                            }}
                                        >
                                            ₹{day.netPnl.toFixed(2)}
                                        </TableCell>
                                        <TableCell align="right">
                                            {day.profitFactor === Infinity ? '∞' : day.profitFactor.toFixed(2)}
                                        </TableCell>
                                        <TableCell align="right">{day.totalTrades}</TableCell>
                                        <TableCell
                                            align="right"
                                            sx={{
                                                color: day.winRate >= 50 ? 'success.main' : 'text.secondary'
                                            }}
                                        >
                                            {day.winRate.toFixed(1)}%
                                        </TableCell>
                                        <TableCell
                                            align="right"
                                            sx={{ color: day.biggestLoss > 0 ? 'error.main' : 'text.secondary' }}
                                        >
                                            {day.biggestLoss > 0 ? `₹${day.biggestLoss.toFixed(2)}` : '-'}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </>
                    ) : (
                        // Individual trades view
                        <>
                            <TableHead>
                                <TableRow>
                                    <TableCell>Entry Time</TableCell>
                                    <TableCell>Exit Time</TableCell>
                                    <TableCell>Duration</TableCell>
                                    <TableCell>Symbol</TableCell>
                                    <TableCell>Qty</TableCell>
                                    <TableCell>Trigger</TableCell>
                                    <TableCell align="right">Entry</TableCell>
                                    <TableCell align="right">Exit</TableCell>
                                    <TableCell align="right">Slippage</TableCell>
                                    <TableCell align="right">Latency</TableCell>
                                    <TableCell align="right">P&L (Net)</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {trades.map((trade) => {
                                    // Calculate duration display
                                    let duration = 'N/A';
                                    if (trade.duration_seconds) {
                                        if (trade.duration_seconds < 60) {
                                            duration = `${trade.duration_seconds.toFixed(1)}s`;
                                        } else if (trade.duration_seconds < 3600) {
                                            duration = `${(trade.duration_seconds / 60).toFixed(1)}m`;
                                        } else {
                                            duration = `${(trade.duration_seconds / 3600).toFixed(1)}h`;
                                        }
                                    }

                                    // Calculate total slippage
                                    const entrySlip = trade.entry_slippage || 0;
                                    const exitSlip = trade.exit_slippage || 0;
                                    const totalSlip = entrySlip + exitSlip;

                                    // Format times (show only HH:MM:SS)
                                    const entryTime = trade.entry_time ? trade.entry_time.substring(11, 19) : 'N/A';
                                    const exitTime = trade.exit_time ? trade.exit_time.substring(11, 19) : 'N/A';

                                    return (
                                        <TableRow key={trade.id || trade.timestamp}>
                                            <TableCell>{entryTime}</TableCell>
                                            <TableCell>{exitTime}</TableCell>
                                            <TableCell>{duration}</TableCell>
                                            <TableCell>{trade.symbol}</TableCell>
                                            <TableCell>{trade.quantity}</TableCell>
                                            <TableCell sx={{
                                                color: trade.trigger_reason?.startsWith('NO-WICK') ? 'warning.main' : 'text.primary',
                                                fontWeight: trade.trigger_reason?.startsWith('NO-WICK') ? 'bold' : 'normal'
                                            }}>
                                                {trade.trigger_reason?.startsWith('NO-WICK') ? '🚀 ' : ''}{trade.trigger_reason}
                                            </TableCell>
                                            <TableCell align="right">{trade.entry_price.toFixed(2)}</TableCell>
                                            <TableCell align="right">{trade.exit_price.toFixed(2)}</TableCell>
                                            <TableCell align="right" sx={{ color: totalSlip !== 0 ? 'warning.main' : 'text.secondary' }}>
                                                {totalSlip !== 0 ? `₹${totalSlip.toFixed(2)}` : '-'}
                                            </TableCell>
                                            <TableCell align="right" sx={{ color: trade.latency_ms > 100 ? 'warning.main' : 'text.secondary' }}>
                                                {trade.latency_ms ? `${trade.latency_ms}ms` : '-'}
                                            </TableCell>
                                            <TableCell align="right" sx={{ color: trade.net_pnl > 0 ? 'success.main' : 'error.main', fontWeight: 'bold' }}>
                                                {typeof trade.net_pnl === 'number' ? trade.net_pnl.toFixed(2) : 'N/A'}
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </>
                    )}
                </Table>
            </TableContainer>
        </Box>
    );
}