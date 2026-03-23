import React, { useState, useEffect } from 'react';
import { 
  FormControl, 
  InputLabel, 
  Select, 
  MenuItem, 
  Box, 
  Alert,
  Snackbar,
  Chip,
  Typography,
  CircularProgress,
  LinearProgress
} from '@mui/material';
import PersonIcon from '@mui/icons-material/Person';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';

const UserSelector = () => {
  const [users, setUsers] = useState([]);
  const [activeUser, setActiveUser] = useState('');
  const [loading, setLoading] = useState(true);
  const [switching, setSwitching] = useState(false);
  const [switchingToUser, setSwitchingToUser] = useState(null);
  const [showAlert, setShowAlert] = useState(false);
  const [alertMessage, setAlertMessage] = useState('');
  const [alertSeverity, setAlertSeverity] = useState('info');

  // Load users on component mount
  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    try {
      const response = await fetch('http://localhost:8000/api/users');
      const data = await response.json();
      
      setUsers(data.users || []);
      setActiveUser(data.active_user || '');
      setLoading(false);
    } catch (error) {
      console.error('Error loading users:', error);
      setAlertMessage('Failed to load users. Using default .env credentials.');
      setAlertSeverity('warning');
      setShowAlert(true);
      setLoading(false);
    }
  };

  const handleUserChange = async (event) => {
    const newUserId = event.target.value;
    const targetUser = users.find(u => u.id === newUserId);
    
    try {
      setSwitching(true);
      setSwitchingToUser(targetUser);
      
      const response = await fetch(`http://localhost:8000/api/users/switch/${newUserId}`, {
        method: 'POST',
      });
      
      const data = await response.json();
      
      if (data.success) {
        setActiveUser(newUserId);
        
        // Check if auto-restart happened
        if (data.auto_restart) {
          setAlertMessage(`✓ ${data.message}`);
          setAlertSeverity('success');
          
          // Wait a moment for bot to restart, then reconnect
          setTimeout(() => {
            window.location.reload(); // Refresh to reconnect with new user
          }, 3000);
        } else if (data.restart_required) {
          setAlertMessage(`✓ ${data.message} Please restart the bot manually.`);
          setAlertSeverity('warning');
        } else {
          setAlertMessage(`✓ ${data.message}`);
          setAlertSeverity('success');
        }
        
        setShowAlert(true);
      } else if (data.error) {
        // Auto-login or other error
        setAlertMessage(`❌ Failed to switch user: ${data.error}`);
        setAlertSeverity('error');
        setShowAlert(true);
      }
    } catch (error) {
      console.error('Error switching user:', error);
      setAlertMessage('❌ Failed to switch user. Is the bot running?');
      setAlertSeverity('error');
      setShowAlert(true);
    } finally {
      setSwitching(false);
      setSwitchingToUser(null);
    }
  };

  if (loading) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary">
          Loading users...
        </Typography>
      </Box>
    );
  }

  // If no users loaded, show fallback message
  if (users.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        <Chip
          icon={<PersonIcon />}
          label="Using .env credentials"
          size="small"
          color="default"
        />
      </Box>
    );
  }

  const currentUser = users.find(u => u.id === activeUser);

  return (
    <Box sx={{ minWidth: 250, p: 2 }}>
      {switching && (
        <Box sx={{ mb: 2 }}>
          <Alert 
            severity="info" 
            icon={<CircularProgress size={20} />}
            sx={{ py: 0.5 }}
          >
            <Typography variant="body2">
              Logging in as <strong>{switchingToUser?.name}</strong>...
            </Typography>
            <Typography variant="caption" color="text.secondary">
              This may take 5-10 seconds
            </Typography>
          </Alert>
          <LinearProgress sx={{ mt: 1 }} />
        </Box>
      )}
      
      <FormControl fullWidth size="small" disabled={switching}>
        <InputLabel id="user-selector-label">Active User</InputLabel>
        <Select
          labelId="user-selector-label"
          id="user-selector"
          value={activeUser}
          label="Active User"
          onChange={handleUserChange}
          startAdornment={<PersonIcon sx={{ mr: 1, color: 'action.active' }} />}
        >
          {users.map((user) => (
            <MenuItem key={user.id} value={user.id}>
              <Box sx={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
                <Typography variant="body2" fontWeight="medium">
                  {user.name}
                </Typography>
                {user.description && (
                  <Typography variant="caption" color="text.secondary">
                    {user.description}
                  </Typography>
                )}
              </Box>
            </MenuItem>
          ))}
        </Select>
      </FormControl>

      {currentUser && (
        <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <SwapHorizIcon fontSize="small" color="action" />
          <Typography variant="caption" color="text.secondary">
            {currentUser.description || currentUser.name}
          </Typography>
        </Box>
      )}

      <Snackbar
        open={showAlert}
        autoHideDuration={6000}
        onClose={() => setShowAlert(false)}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setShowAlert(false)}
          severity={alertSeverity}
          variant="filled"
          sx={{ width: '100%' }}
        >
          {alertMessage}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default UserSelector;
