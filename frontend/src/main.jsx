import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import ErrorBoundary from './ErrorBoundary.jsx';
import './index.css';
import { SnackbarProvider } from 'notistack';

// Disable StrictMode completely to prevent double WebSocket connections
// StrictMode intentionally double-mounts components in development to detect side effects
// This causes WebSocket connections to disconnect immediately (0.0s duration)
// For trading bots with real-time WebSocket connections, StrictMode is problematic
const AppWrapper = () => (
  <ErrorBoundary name="Root App">
    <SnackbarProvider maxSnack={3} anchorOrigin={{ vertical: 'top', horizontal: 'center' }}>
      <App />
    </SnackbarProvider>
  </ErrorBoundary>
);

ReactDOM.createRoot(document.getElementById('root')).render(<AppWrapper />);