import React, { memo } from 'react';
import { Paper, Typography, TableContainer, Table, TableHead, TableBody, TableRow, TableCell, Tooltip } from '@mui/material';
import { useStore } from '../store/store';

// Memoized component to prevent re-renders when data unchanged
const OptionChain = memo(({ data }) => {
    const indexPrice = useStore(state => state.botStatus.indexPrice);

    const getRowStyle = (strike) => {
        const diff = Math.abs(strike - indexPrice);
        if (diff < 100) return { backgroundColor: 'rgba(255, 255, 0, 0.1)' }; // ATM
        return {};
    };
    
    // Helper function to format valuation display
    const formatValuation = (valuation) => {
        if (valuation === 0 || valuation === undefined) return '';
        return valuation > 0 ? `+${valuation}%` : `${valuation}%`;
    };
    
    // Helper function to format price change display
    const formatPriceChange = (change) => {
        if (change === '--' || change === 0 || change === undefined) return '';
        return change > 0 ? `+₹${change}` : `₹${change}`;
    };
    
    // The JSX return block with IV and Valuation columns
    return (
        <Paper elevation={3} sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 1 }}>
                Option Chain (IV-Based Valuation)
            </Typography>
            <TableContainer sx={{ maxHeight: 250 }}>
                <Table stickyHeader size="small">
                    <TableHead>
                        <TableRow>
                            <TableCell align="center" sx={{color: 'success.main', fontSize: '0.75rem'}}>LTP</TableCell>
                            <TableCell align="center" sx={{color: 'success.main', fontSize: '0.7rem'}}>Fair</TableCell>
                            <TableCell align="center" sx={{color: 'success.main', fontSize: '0.7rem'}}>Exp Δ</TableCell>
                            <TableCell align="center" sx={{ fontWeight: 'bold', fontSize: '0.85rem' }}>Strike</TableCell>
                            <TableCell align="center" sx={{color: 'error.main', fontSize: '0.7rem'}}>Exp Δ</TableCell>
                            <TableCell align="center" sx={{color: 'error.main', fontSize: '0.7rem'}}>Fair</TableCell>
                            <TableCell align="center" sx={{color: 'error.main', fontSize: '0.75rem'}}>LTP</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {data.map((row) => (
                            <TableRow key={row.strike} sx={getRowStyle(row.strike)}>
                                {/* CE LTP */}
                                <TableCell 
                                    align="center" 
                                    sx={{
                                        backgroundColor: row.ce_color || 'rgba(255, 255, 255, 0)',
                                        fontSize: '0.8rem',
                                        fontWeight: 'bold'
                                    }}
                                >
                                    {row.ce_ltp !== '--' ? `₹${row.ce_ltp}` : '--'}
                                </TableCell>
                                
                                {/* CE Fair Price */}
                                <TableCell 
                                    align="center" 
                                    sx={{
                                        fontSize: '0.75rem',
                                        color: 'info.main',
                                        fontStyle: 'italic'
                                    }}
                                >
                                    {row.ce_fair !== '--' ? `₹${row.ce_fair}` : '--'}
                                </TableCell>
                                
                                {/* CE Expected Change */}
                                <Tooltip title={row.ce_exp_change < 0 ? 'Expected to DROP' : row.ce_exp_change > 0 ? 'Expected to RISE' : ''} arrow>
                                    <TableCell 
                                        align="center" 
                                        sx={{
                                            fontSize: '0.75rem',
                                            fontWeight: 'bold',
                                            color: row.ce_exp_change < 0 ? 'error.main' : row.ce_exp_change > 0 ? 'success.main' : 'text.secondary'
                                        }}
                                    >
                                        {formatPriceChange(row.ce_exp_change)}
                                    </TableCell>
                                </Tooltip>
                                
                                {/* Strike */}
                                <TableCell align="center" sx={{ fontWeight: 'bold', fontSize: '0.9rem' }}>
                                    {row.strike}
                                </TableCell>
                                
                                {/* PE Expected Change */}
                                <Tooltip title={row.pe_exp_change < 0 ? 'Expected to DROP' : row.pe_exp_change > 0 ? 'Expected to RISE' : ''} arrow>
                                    <TableCell 
                                        align="center" 
                                        sx={{
                                            fontSize: '0.65rem',
                                            fontWeight: 'bold',
                                            color: row.pe_exp_change < 0 ? 'error.main' : row.pe_exp_change > 0 ? 'success.main' : 'text.secondary'
                                        }}
                                    >
                                        {formatPriceChange(row.pe_exp_change)}
                                    </TableCell>
                                </Tooltip>
                                
                                {/* PE Fair Price */}
                                <TableCell 
                                    align="center" 
                                    sx={{
                                        fontSize: '0.65rem',
                                        color: 'info.main',
                                        fontStyle: 'italic'
                                    }}
                                >
                                    {row.pe_fair !== '--' ? `₹${row.pe_fair}` : '--'}
                                </TableCell>
                                
                                {/* PE LTP */}
                                <TableCell 
                                    align="center" 
                                    sx={{
                                        backgroundColor: row.pe_color || 'rgba(255, 255, 255, 0)',
                                        fontSize: '0.75rem',
                                        fontWeight: 'bold'
                                    }}
                                >
                                    {row.pe_ltp !== '--' ? `₹${row.pe_ltp}` : '--'}
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </TableContainer>
            
            {/* Legend */}
            <Typography variant="caption" sx={{ mt: 1, display: 'block', color: 'text.secondary' }}>
                <strong>Fair</strong> = Expected price (Put-Call Parity) | 
                <strong>Exp Δ</strong> = Expected change | 
                <span style={{ color: '#00ff00', fontWeight: 'bold' }}> Green BG = Undervalued</span> | 
                <span style={{ color: '#ff0000', fontWeight: 'bold' }}> Red BG = Overvalued</span>
            </Typography>
        </Paper>
    );
});

OptionChain.displayName = 'OptionChain';

export default OptionChain;