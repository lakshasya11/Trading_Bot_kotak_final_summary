import React, { useState, useEffect } from 'react';
import './App.css';

function App() {
  const [showSplash, setShowSplash] = useState(true);
  const [isSignUp, setIsSignUp] = useState(false);
  const [currentPage, setCurrentPage] = useState('login');
  const [resetEmail, setResetEmail] = useState('');
  const [showTOTP, setShowTOTP] = useState(false);
  const [loginCredentials, setLoginCredentials] = useState({});
  const [showQRCode, setShowQRCode] = useState(false);
  const [qrData, setQrData] = useState({});
  const [emailVerified, setEmailVerified] = useState(false);
  const [verificationEmail, setVerificationEmail] = useState('');
  const [showOTPInput, setShowOTPInput] = useState(false);
  const [users, setUsers] = useState([]);
  const [showPassword, setShowPassword] = useState(false);
  const [showSignupPassword, setShowSignupPassword] = useState(false);
  const [mousePosition, setMousePosition] = useState({ x: 0, y: 0 });

  const handleMouseMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width - 0.5) * 30;
    const y = ((e.clientY - rect.top) / rect.height - 0.5) * -30;
    setMousePosition({ x, y });
  };

  const validatePassword = (password) => {
    const regex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,15}$/;
    return regex.test(password) && !password.includes(' ');
  };

  const validateAadhar = (aadhar) => {
    return /^\d{12}$/.test(aadhar);
  };

  const handleSignup = async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    const userData = {
      client_id: formData.get('clientId'),
      firstName: formData.get('firstName'),
      lastName: formData.get('lastName'),
      email: formData.get('email'),
      mobile: formData.get('mobile'),
      password: formData.get('password'),
      aadhar: formData.get('aadhar')
    };

    if (!validatePassword(userData.password)) {
      alert('Password must be 8-15 characters with uppercase, lowercase, number, and special character');
      return;
    }

    if (!validateAadhar(userData.aadhar)) {
      alert('Aadhar must be exactly 12 digits');
      return;
    }

    if (!emailVerified) {
      alert('Please verify your email first');
      return;
    }

    try {
      const response = await fetch('http://localhost:5001/api/signup', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(userData)
      });

      const data = await response.json();

      if (data.success) {
        setQrData({
          qrCode: data.qr_code,
          secret: data.secret,
          username: data.username
        });
        setShowQRCode(true);
      } else {
        alert('Signup failed: ' + data.error);
      }
    } catch (error) {
      alert('Error during signup. Please try again.');
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    const loginData = {
      clientId: formData.get('clientId'),
      password: formData.get('password'),
      totpCode: formData.get('totpCode')
    };

    try {
      const response = await fetch('http://localhost:5001/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(loginData)
      });

      const data = await response.json();
      if (data.success) {
        window.location.href = 'http://localhost:5173/';
        setShowTOTP(false);
      } else if (data.require_totp) {
        setLoginCredentials({
          clientId: loginData.clientId,
          password: loginData.password
        });
        setCurrentPage('totp');
      } else {
        alert('Login failed: ' + data.error);
      }
    } catch (error) {
      console.error('Login error:', error);
      alert('Error during login. Make sure backend is running on port 5000.');
    }
  };

  const handleTOTPSubmit = async (e) => {
    e.preventDefault();
    const totpCode = e.target.totpCode.value;

    try {
      const response = await fetch('http://localhost:5001/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...loginCredentials,
          totpCode
        })
      });

      const data = await response.json();

      if (data.success) {
        window.location.href = 'http://localhost:5173/';
        setShowTOTP(false);
      }
      else {
        alert('Invalid TOTP code: ' + data.error);
      }
    } catch (error) {
      console.error('TOTP error:', error);
      alert('Error verifying TOTP. Check console for details.');
    }
  };

  const handleForgotPassword = async (e) => {
    e.preventDefault();
    const email = e.target.email.value;

    if (!email) {
      alert('Please enter email address');
      return;
    }

    try {
      const response = await fetch('http://localhost:5001/api/send-otp', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email })
      });

      const data = await response.json();

      if (data.success) {
        setResetEmail(email);
        setCurrentPage('verify-otp');
      } else {
        alert('Failed to send OTP: ' + data.error);
      }
    } catch (error) {
      console.error('Error:', error);
      alert('Error sending OTP. Make sure backend is running.');
    }
  };

  const handleVerifyOTP = async (e) => {
    e.preventDefault();
    const otp = e.target.otp.value;

    try {
      const response = await fetch('http://localhost:5001/api/verify-otp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: resetEmail, otp })
      });

      const data = await response.json();

      if (data.success) {
        setCurrentPage('reset-password');
      } else {
        alert('Invalid OTP: ' + data.error);
      }
    } catch (error) {
      alert('Error verifying OTP.');
    }
  };

  const handleResetPassword = async (e) => {
    e.preventDefault();
    const newPassword = e.target.newPassword.value;
    const confirmPassword = e.target.confirmPassword.value;

    if (newPassword !== confirmPassword) {
      alert('Passwords do not match');
      return;
    }

    if (!validatePassword(newPassword)) {
      alert('Password must be 8-15 characters with uppercase, lowercase, number, and special character');
      return;
    }

    try {
      const response = await fetch('http://localhost:5001/api/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: resetEmail, newPassword })
      });

      const data = await response.json();

      if (data.success) {
        alert('Password reset successfully!');
        setCurrentPage('login');
        setResetEmail('');
      } else {
        alert('Failed to reset password: ' + data.error);
      }
    } catch (error) {
      alert('Error resetting password.');
    }
  };

  const handleEmailVerifySignup = async () => {
    const email = document.querySelector('input[name="email"]').value;
    if (!email) {
      alert('Please enter email first');
      return;
    }

    try {
      const response = await fetch('http://localhost:5001/api/send-email-verification', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email })
      });

      const data = await response.json();

      if (data.success) {
        setVerificationEmail(email);
        setShowOTPInput(true);
        alert('Verification OTP sent to your email!');
      } else {
        alert('Failed to send OTP: ' + data.error);
      }
    } catch (error) {
      alert('Error sending verification OTP.');
    }
  };

  const handleVerifyEmailOTP = async (e) => {
    const otp = e.target.emailOtp.value;

    try {
      const response = await fetch('http://localhost:5001/api/verify-email-otp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: verificationEmail, otp })
      });

      const data = await response.json();

      if (data.success) {
        setEmailVerified(true);
        setShowOTPInput(false);
        alert('Email verified successfully!');
      } else {
        alert('Invalid OTP: ' + data.error);
      }
    } catch (error) {
      alert('Error verifying email OTP.');
    }
  };

  const handleEmailVerify = async () => {
    const email = document.querySelector('input[name="email"]').value;
    if (!email) {
      alert('Please enter email first');
      return;
    }

    try {
      const response = await fetch('http://localhost:5001/api/send-otp', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email })
      });

      const data = await response.json();

      if (data.success) {
        alert('OTP sent to your email! Please check your inbox.');
      } else {
        alert('Failed to send OTP: ' + data.error);
      }
    } catch (error) {
      console.error('Error:', error);
      alert('Error sending OTP. Make sure backend is running on port 5000.');
    }
  };

  if (showQRCode) {
    return (
      <div className="container">
        <div className="qr-modal">
          <div className="qr-content">
            <h2>Account Created Successfully!</h2>
            <p>Save your unique QR code and key for future reference</p>
            <div className="qr-container">
              <img src={`data:image/png;base64,${qrData.qrCode}`} alt="QR Code" className="qr-image" />
            </div>
            <div className="key-section">
              <p className="key-label">Your Unique Key:</p>
              <p className="key-value">{qrData.secret}</p>
            </div>
            <button
              className="continue-btn"
              onClick={() => {
                setShowQRCode(false);
                setIsSignUp(false);
                setCurrentPage('login');
              }}
            >
              Continue to Login
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (currentPage === 'totp') {
    return (
      <div className="split-screen-container flip-entrance">
        {/* Left Side: The Lock Animation */}
        <div className="animation-side">
          <div className="security-visual-container">
            {/* Dynamic Background Elements */}
            <div className="cyber-grid"></div>
            <div className="scanning-line"></div>

            {/* Central Lock Unit */}
            <div className="lock-system-core">
              <div className="shackle-3d"></div>
              <div className="lock-chassis">
                {/* The central pulsing blue "eye" or portal */}
                <div className="biometric-scanner">
                  <div className="inner-glow"></div>
                  <div className="pulse-ring"></div>
                </div>
              </div>
              {/* Soft floor shadow for 3D grounding */}
              <div className="floor-shadow"></div>
            </div>

            <div className="label-container">
              <h2 className="neon-text">SECURE <span className="cyan-glow">ACCESS</span></h2>
              <div className="status-blink">SYSTEM AUTHENTICATION ACTIVE</div>
            </div>
          </div>
        </div>

        {/* Right Side: The Form */}
        <div className="form-side">
          <div className="totp-card glass-morphism">
            <div className="totp-header">
              <h1>Trading Bot</h1>
              <h2>Two-Factor Authentication</h2>
              <p>Enter the 6-digit code from your authenticator app</p>
            </div>

            <form onSubmit={handleTOTPSubmit} className="totp-form">
              <div className="totp-input-group">
                <i className="fas fa-shield-alt"></i>
                <input
                  type="text"
                  name="totpCode"
                  placeholder="000000"
                  maxLength="6"
                  className="totp-input hud-style"
                  required
                />
              </div>
              <button type="submit" className="totp-btn neon-glow">
                Verify & Login
              </button>
            </form>

            <div className="totp-footer">
              <a href="#" onClick={() => setCurrentPage('login')}>← Back to Login</a>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (currentPage === 'verify-otp') {
    return (
      <div className="galaxy-bg">
        {/* Background Animation Elements */}
        <div className="stars-container">
          {[...Array(20)].map((_, i) => (
            <div key={i} className="shooting-star"></div>
          ))}
        </div>

        <div className="forgot-container glass-card">
          <h2>Verification Code</h2>
          <p>Enter the 6-digit OTP sent to <br /><strong>{resetEmail}</strong></p>
          <form onSubmit={handleVerifyOTP}>
            <div className="input-group">
              <i className="fas fa-key"></i>
              <input type="text" name="otp" placeholder="000000" maxLength="6" required />
            </div>
            <button type="submit" className="btn-primary neon-btn">Verify OTP</button>
          </form>
          <div className="links">
            <a href="#" onClick={() => setCurrentPage('login')}>← Back to Login</a>
          </div>
        </div>
      </div>
    );
  }

  if (currentPage === 'reset-password') {
    return (
      <div className="reset-password-page">
        {/* Background Animated Circles */}
        <div className="corner-circle top-right">
          <div className="star-inner"></div>
        </div>
        <div className="corner-circle bottom-left">
          <div className="star-inner"></div>
        </div>

        {/* Star Dotted Background Overlay */}
        <div className="star-dots-overlay"></div>

        <div className="forgot-container glass-card card-slide-in">
          <h2>Reset Password</h2>
          <p>Enter your new password</p>
          <form onSubmit={handleResetPassword}>
            <div className="input-group">
              <i className="fas fa-lock"></i>
              <input type="password" name="newPassword" placeholder="New Password" required />
            </div>
            <div className="input-group">
              <i className="fas fa-lock"></i>
              <input type="password" name="confirmPassword" placeholder="Confirm Password" required />
            </div>
            <button type="submit" className="btn-primary neon-btn">Submit</button>
          </form>
        </div>
      </div>
    );
  }

  if (currentPage === 'forgot') {
    return (
      <div className="forgot-3d-scene page-enter">
        {/* 3D Orbiting Ring */}
        <div className="orbit-viewport">
          <div className="orbit-ring">
            {[...Array(10)].map((_, i) => (
              <div key={i} className={`orbit-token token-${i}`}>
                <div className="token-face">
                  {i % 2 === 0 ? '₿' : 'Ξ'}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Centered Forgot Password Form */}
        <div className="forgot-ui-overlay">
          <div className="forgot-card glass-morphism card-slide-in">
            <div className="totp-header">
              <h1>Forgot Password</h1>
              <h2>Account Recovery</h2>
              <p>Enter your email to receive a secure OTP</p>
            </div>

            <form onSubmit={handleForgotPassword} className="totp-form">
              <div className="totp-input-group">
                <i className="fas fa-envelope"></i>
                <input
                  type="email"
                  name="email"
                  placeholder="email@example.com"
                  className="totp-input hud-style"
                  required
                />
              </div>
              <button type="submit" className="totp-btn neon-glow">
                Send OTP
              </button>
            </form>

            <div className="totp-footer">
              <a href="#" onClick={() => setCurrentPage('login')}>← Back to Login</a>
            </div>
          </div>
        </div>

        {/* Background Glows */}
        <div className="bg-glow-top"></div>
        <div className="bg-glow-bottom"></div>
      </div>
    );
  }

  if (showSplash) {
    return (
      <div className="splash-screen" onClick={() => setShowSplash(false)}>
        {/* Galaxy Background */}
        <div className="galaxy-container"></div>

        {/* Stars */}
        <div className="stars-container">
          <div className="star"></div>
          <div className="star"></div>
          <div className="star"></div>
          <div className="star"></div>
          <div className="star"></div>
        </div>

        {/* Trading Candles */}
        <div className="trading-candles">
          <div className="candle"></div>
          <div className="candle"></div>
          <div className="candle"></div>
          <div className="candle"></div>
          <div className="candle"></div>
          <div className="candle"></div>
          <div className="candle"></div>
          <div className="candle"></div>
          <div className="candle"></div>
        </div>

        {/* Graph Lines */}
        <div className="graph-lines">
          <div className="graph-line"></div>
          <div className="graph-line"></div>
          <div className="graph-line"></div>
          <div className="graph-line"></div>
        </div>

        {/* Data Points */}
        <div className="data-points">
          <div className="data-point"></div>
          <div className="data-point"></div>
          <div className="data-point"></div>
          <div className="data-point"></div>
          <div className="data-point"></div>
        </div>

        {/* Nebula Clouds */}
        <div className="nebula-cloud nebula-1"></div>
        <div className="nebula-cloud nebula-2"></div>
        <div className="nebula-cloud nebula-3"></div>

        {/* Existing Content */}
        <div className="pulsing-circle">
          <div className="circle-text">TRADING BOT</div>
          <div className="circle-glow"></div>
        </div>
        <p className="click-to-enter">CLICK TO INITIALIZE SYSTEM</p>
      </div>
    );
  }

  return (
    <div className={`container page-transition ${!showSplash ? 'slide-open' : ''}`}>
      {/* DB Viewer Button */}
      <button
        onClick={() => window.open('http://localhost:8000/db_viewer.html', '_blank')}
        style={{
          position: 'fixed',
          top: '20px',
          right: '20px',
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          color: 'white',
          border: 'none',
          padding: '10px 20px',
          borderRadius: '25px',
          cursor: 'pointer',
          fontWeight: 'bold',
          fontSize: '14px',
          zIndex: 1000,
          boxShadow: '0 4px 15px rgba(0,0,0,0.2)'
        }}
      >
        📊 DB Viewer
      </button>

      {/* Galaxy Background */}
      <div className="galaxy-container"></div>

      {/* Stars */}
      <div className="stars-container">
        <div className="star"></div>
        <div className="star"></div>
        <div className="star"></div>
        <div className="star"></div>
        <div className="star"></div>
      </div>

      {/* Trading Candles */}
      <div className="trading-candles">
        <div className="candle"></div>
        <div className="candle"></div>
        <div className="candle"></div>
        <div className="candle"></div>
        <div className="candle"></div>
        <div className="candle"></div>
        <div className="candle"></div>
        <div className="candle"></div>
        <div className="candle"></div>
      </div>

      {/* Graph Lines */}
      <div className="graph-lines">
        <div className="graph-line"></div>
        <div className="graph-line"></div>
        <div className="graph-line"></div>
        <div className="graph-line"></div>
      </div>

      {/* Data Points */}
      <div className="data-points">
        <div className="data-point"></div>
        <div className="data-point"></div>
        <div className="data-point"></div>
        <div className="data-point"></div>
        <div className="data-point"></div>
      </div>

      {/* Nebula Clouds */}
      <div className="nebula-cloud nebula-1"></div>
      <div className="nebula-cloud nebula-2"></div>
      <div className="nebula-cloud nebula-3"></div>

      <div className={`auth-container ${isSignUp ? 'sign-up-mode' : ''}`}>
        <div className="forms-container">
          <div className="signin-signup">
            <form className="sign-in-form" onSubmit={handleLogin}>
              <h2 className="title">Login</h2>
              <div className="input-field">
                <i className="fas fa-id-badge"></i>
                <input type="text" name="clientId" placeholder="Client ID" required />
              </div>
              <div className="input-field password-field">
                <i className="fas fa-lock"></i>
                <input type={showPassword ? "text" : "password"} name="password" placeholder="Password" required />
                <i className={`fas ${showPassword ? 'fa-eye-slash' : 'fa-eye'} password-toggle`} onClick={() => setShowPassword(!showPassword)}></i>
              </div>

              <input type="submit" value="Login" className="btn solid" />
              <p className="social-text">
                <a href="#" onClick={() => setCurrentPage('forgot')}>Forgot Password?</a>
              </p>
              <p className="social-text">
                Don't have an account? <a href="#" onClick={async () => {
                  try {
                    const response = await fetch('http://localhost:5001/api/check-signup-allowed');
                    const data = await response.json();
                    if (!data.signupAllowed) {
                      alert('User already exists. New signup not allowed.');
                    } else {
                      setIsSignUp(true);
                    }
                  } catch (error) {
                    setIsSignUp(true);
                  }
                }}>Sign up</a>
              </p>
            </form>

            <form className="sign-up-form" onSubmit={handleSignup}>
              <h2 className="title">Sign up</h2>
              <div className="input-field">
                <i className="fas fa-id-badge"></i>
                <input type="text" name="clientId" placeholder="Client ID" required />
              </div>
              <div className="input-field">
                <i className="fas fa-user"></i>
                <input type="text" name="firstName" placeholder="First Name" required />
              </div>
              <div className="input-field">
                <i className="fas fa-user"></i>
                <input type="text" name="lastName" placeholder="Last Name" required />
              </div>
              <div className="email-container">
                <div className="input-field email-field">
                  <i className="fas fa-envelope"></i>
                  <input type="email" name="email" placeholder="Email" required />
                </div>
                <button type="button" className="verify-btn" onClick={handleEmailVerifySignup}>Verify</button>
              </div>
              {emailVerified && (
                <div className="verified-status">
                  <i className="fas fa-check-circle"></i>
                  <span>Email Verified</span>
                </div>
              )}
              {showOTPInput && (
                <div className="otp-verification">
                  <div className="input-field">
                    <i className="fas fa-key"></i>
                    <input type="text" name="emailOtp" placeholder="Enter Email OTP" maxLength="6" required />
                  </div>
                  <button type="button" className="verify-otp-btn" onClick={(e) => {
                    e.preventDefault();
                    const otp = document.querySelector('input[name="emailOtp"]').value;
                    handleVerifyEmailOTP({ target: { emailOtp: { value: otp } } });
                  }}>Verify OTP</button>
                </div>
              )}
              <div className="input-field">
                <i className="fas fa-phone"></i>
                <input type="tel" name="mobile" placeholder="Mobile Number" required />
              </div>
              <div className="input-field password-field">
                <i className="fas fa-lock"></i>
                <input type={showSignupPassword ? "text" : "password"} name="password" placeholder="Password" required />
                <i className={`fas ${showSignupPassword ? 'fa-eye-slash' : 'fa-eye'} password-toggle`} onClick={() => setShowSignupPassword(!showSignupPassword)}></i>
              </div>
              <div className="input-field">
                <i className="fas fa-id-card"></i>
                <input type="text" name="aadhar" placeholder="Aadhar Number (12 digits)" maxLength="12" pattern="[0-9]{12}" onInput={(e) => e.target.value = e.target.value.replace(/[^0-9]/g, '')} required />
              </div>
              <input type="submit" className="btn" value="Sign up" />
              <p className="social-text">
                Already have an account? <a href="#" onClick={() => setIsSignUp(false)}>Login</a>
              </p>
            </form>
          </div>
        </div>

        <div className="panels-container">
          <div className="panel left-panel">
          </div>
          <div className="panel right-panel" onMouseMove={handleMouseMove}>
            <div className="content">
              <div className="blob-3d">
                <div className="blob-container" style={{
                  transform: `rotateX(${mousePosition.y}deg) rotateY(${mousePosition.x}deg)`
                }}>
                  <div className="blob-body">
                    <div className="blob-face">
                      <div className="blob-eye left-eye"></div>
                      <div className="blob-eye right-eye"></div>
                      <div className="blob-mouth"></div>
                    </div>
                    <div className="blob-belly"></div>
                  </div>
                  <div className="blob-antennas">
                    <div className="antenna antenna-1"></div>
                    <div className="antenna antenna-2"></div>
                  </div>
                  <div className="glow-ring"></div>
                </div>
              </div>
              <h3>Join Our Community</h3>
              <p>Secure account with email verification and 2FA protection.</p>
              <button className="btn transparent" onClick={() => setIsSignUp(false)}>Sign in</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;