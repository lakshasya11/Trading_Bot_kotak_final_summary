import React from 'react';
import { ThemeProvider, createTheme, CssBaseline, Box, Typography, Grid, Paper } from '@mui/material';

const lightTheme = createTheme({
    palette: { mode: 'light', primary: { main: '#1976d2' } },
});

function AppMinimal() {
    return (
        <ThemeProvider theme={lightTheme}>
            <CssBaseline />
            <Box sx={{ p: 2, minHeight: '100vh', bgcolor: '#f4f6f8' }}>
                <Typography variant="h4" sx={{ mb: 2, color: 'primary.main' }}>
                    ✅ Material-UI Test
                </Typography>
                <Grid container spacing={2}>
                    <Grid item xs={12} md={6}>
                        <Paper sx={{ p: 2 }}>
                            <Typography variant="h6">Panel 1</Typography>
                            <Typography>If you can see this, Material-UI is working!</Typography>
                        </Paper>
                    </Grid>
                    <Grid item xs={12} md={6}>
                        <Paper sx={{ p: 2 }}>
                            <Typography variant="h6">Panel 2</Typography>
                            <Typography>Grid layout is working correctly.</Typography>
                        </Paper>
                    </Grid>
                </Grid>
            </Box>
        </ThemeProvider>
    );
}

export default AppMinimal;
