import React, { useEffect, useRef } from 'react';
import { createChart, ColorType } from 'lightweight-charts';
import { Paper, Typography, Box, CircularProgress } from '@mui/material';

// We create a single configuration for both charts to share
const commonChartOptions = {
    layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#333',
    },
    grid: { 
        vertLines: { color: '#f0f0f0' }, 
        horzLines: { color: '#f0f0f0' } 
    },
    timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: '#D1D4DC',
        tickMarkFormatter: (time) => {
            const date = new Date(time * 1000);
            return date.toLocaleTimeString('en-IN', { 
                timeZone: 'Asia/Kolkata', 
                hour: '2-digit', 
                minute: '2-digit', 
                hour12: false 
            });
        },
    },
    crosshair: { 
        mode: 1 
    },
    rightPriceScale: { 
        borderColor: '#D1D4DC' 
    },
};

export default function IndexChart({ data }) {
    // Create separate refs for each chart container and instance
    const rsiChartContainerRef = useRef(null);
    const priceChartContainerRef = useRef(null);
    const rsiChartRef = useRef(null);
    const priceChartRef = useRef(null);
    const seriesRef = useRef({});

    useEffect(() => {
        if (!rsiChartContainerRef.current || !priceChartContainerRef.current) return;

        // --- CREATE TWO SEPARATE CHART INSTANCES ---
        const rsiChart = createChart(rsiChartContainerRef.current, {
            ...commonChartOptions,
            width: rsiChartContainerRef.current.clientWidth,
            height: rsiChartContainerRef.current.clientHeight,
        });

        const priceChart = createChart(priceChartContainerRef.current, {
            ...commonChartOptions,
            width: priceChartContainerRef.current.clientWidth,
            height: priceChartContainerRef.current.clientHeight,
            timeScale: {
                // Hide the time scale on the bottom chart to avoid duplication
                visible: false, 
            }
        });

        rsiChartRef.current = rsiChart;
        priceChartRef.current = priceChart;

        // --- ADD SERIES TO THEIR RESPECTIVE CHARTS ---
        seriesRef.current.rsiSeries = rsiChart.addLineSeries({ color: 'rgba(136, 132, 216, 0.7)', lineWidth: 2, title: 'RSI' });
        seriesRef.current.rsiSmaSeries = rsiChart.addLineSeries({ color: '#f5a623', lineWidth: 2, title: 'RSI SMA' });

        seriesRef.current.candlestickSeries = priceChart.addCandlestickSeries({ upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350' });
        seriesRef.current.supertrendSeries = priceChart.addLineSeries({ color: '#9C27B0', lineWidth: 3, title: 'Supertrend' });

        // --- CRITICAL: SYNCHRONIZE THE TWO CHARTS ---
        const syncTimeScales = (sourceChart, targetChart) => (range) => {
            if (range) {
                 targetChart.timeScale().setVisibleLogicalRange(range);
            }
        };
        rsiChart.timeScale().subscribeVisibleLogicalRangeChange(syncTimeScales(rsiChart, priceChart));
        priceChart.timeScale().subscribeVisibleLogicalRangeChange(syncTimeScales(priceChart, rsiChart));

        // Cleanup function
        return () => {
            // Unsubscribe from events to prevent memory leaks
            rsiChart.timeScale().unsubscribeVisibleLogicalRangeChange(syncTimeScales(rsiChart, priceChart));
            priceChart.timeScale().unsubscribeVisibleLogicalRangeChange(syncTimeScales(priceChart, rsiChart));
            rsiChart.remove();
            priceChart.remove();
        };
    }, []);

    // This data update effect remains largely the same
    useEffect(() => {
        if (!data || Object.keys(seriesRef.current).length < 4) return;

        if (data.candles) seriesRef.current.candlestickSeries.setData(data.candles);
        if (data.supertrend) seriesRef.current.supertrendSeries.setData(data.supertrend);
        if (data.rsi) seriesRef.current.rsiSeries.setData(data.rsi);
        if (data.rsi_sma) seriesRef.current.rsiSmaSeries.setData(data.rsi_sma);
        
        if (data.candles && data.candles.length > 0) {
            rsiChartRef.current.timeScale();
        }
    }, [data]);
    
    // This resize effect needs to update both charts
    useEffect(() => {
        const handleResize = () => {
            if (rsiChartRef.current && rsiChartContainerRef.current) {
                rsiChartRef.current.resize(rsiChartContainerRef.current.clientWidth, rsiChartContainerRef.current.clientHeight);
            }
            if (priceChartRef.current && priceChartContainerRef.current) {
                priceChartRef.current.resize(priceChartContainerRef.current.clientWidth, priceChartContainerRef.current.clientHeight);
            }
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);


    return (
        <Paper elevation={3} sx={{ p: 2, height: '450px', display: 'flex', flexDirection: 'column' }}>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, mb: 1, alignItems: 'center', flexShrink: 0 }}>
                <Typography variant="body2">Index Chart</Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Box sx={{ width: 12, height: 12, backgroundColor: '#9C27B0' }} />
                    <Typography variant="caption">Supertrend</Typography>
                </Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Box sx={{ width: 12, height: 12, backgroundColor: 'rgba(136, 132, 216, 0.7)' }} />
                    <Typography variant="caption">RSI</Typography>
                </Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Box sx={{ width: 12, height: 12, backgroundColor: '#f5a623' }} />
                    <Typography variant="caption">RSI SMA</Typography>
                </Box>
            </Box>
            
            <Box sx={{ width: '100%', flexGrow: 1, display: 'flex', flexDirection: 'column', position: 'relative' }}>
                {!data && (
                    <Box sx={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <CircularProgress />
                    </Box>
                )}
                {/* Top Pane: 40% height */}
                <Box ref={rsiChartContainerRef} sx={{ width: '100%', height: '30%' }} />
                {/* Bottom Pane: 60% height */}
                <Box ref={priceChartContainerRef} sx={{ width: '100%', height: '70%' }} />
            </Box>
        </Paper>
    );
}