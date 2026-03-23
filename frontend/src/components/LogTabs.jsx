import React, { useState } from 'react';
import { Paper, Box, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tabs, Tab, Typography } from '@mui/material';
import AnalyticsPanel from './AnalyticsPanel'; // NEW: Import the reusable component

function TabPanel(props) {
    const { children, value, index, ...other } = props;
    const isActive = value === index;
    console.log(`TabPanel ${index} - Active: ${isActive}`);
    return (
        <div role="tabpanel" hidden={!isActive} style={{ height: '100%', display: isActive ? 'block' : 'none' }} {...other}>
            <Box sx={{ p: 1, height: '100%' }}>{children}</Box>
        </div>
    );
}

// CHANGED: Component no longer needs `tradeHistory` prop
export default function LogTabs({ debugLogs }) {
    const [value, setValue] = useState(0);
    const handleChange = (event, newValue) => {
        console.log(`📑 Tab switched from ${value} to ${newValue}`);
        setValue(newValue);
    };

    return (
        <Paper elevation={3} sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
                <Tabs value={value} onChange={handleChange}>
                    {/* CHANGED: Tab labels are updated */}
                    <Tab label="Debug Log" />
                    <Tab label="Today's Report" />
                    <Tab label="Overall Performance" />
                </Tabs>
            </Box>
            
            <TabPanel value={value} index={0}>
                <TableContainer sx={{ maxHeight: 750 }}>
                    <Table stickyHeader size="small">
                        <TableHead><TableRow><TableCell>Time</TableCell><TableCell>Source</TableCell><TableCell>Message</TableCell></TableRow></TableHead>
                        <TableBody>
                            {debugLogs && debugLogs.length > 0 ? (
                                debugLogs.map((log, i) => (
                                    <TableRow key={i}>
                                        <TableCell>{log.time}</TableCell>
                                        <TableCell>{log.source}</TableCell>
                                        <TableCell>{log.message}</TableCell>
                                    </TableRow>
                                ))
                            ) : (
                                <TableRow>
                                    <TableCell colSpan={3} align="center" sx={{ py: 4 }}>
                                        <Typography variant="body2" color="text.secondary">
                                            No debug logs yet. Start the bot to see live trading activity.
                                        </Typography>
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
            </TabPanel>

            <TabPanel value={value} index={1}>
                {/* NEW: Use the reusable panel for today's data */}
                <AnalyticsPanel scope="today" viewType="trades" />
            </TabPanel>
            
            <TabPanel value={value} index={2}>
                {/* NEW: Use the reusable panel for all data with date-wise view */}
                <AnalyticsPanel scope="all" viewType="daily" />
            </TabPanel>
        </Paper>
    );
}