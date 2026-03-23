const express = require('express');
const cors = require('cors');
const nodemailer = require('nodemailer');
require('dotenv').config();

const app = express();
const PORT = process.env.PORT || 5000;

app.use(cors());
app.use(express.json());

// Store OTPs temporarily (use Redis/database in production)
const otpStore = new Map();
const users = [];

// Email transporter
const transporter = nodemailer.createTransporter({
  service: 'gmail',
  auth: {
    user: process.env.EMAIL_USER,
    pass: process.env.EMAIL_PASS
  }
});

// Send OTP endpoint
app.post('/api/send-otp', async (req, res) => {
  const { email } = req.body;
  
  if (!email) {
    return res.status(400).json({ error: 'Email is required' });
  }

  const otp = Math.floor(100000 + Math.random() * 900000);
  otpStore.set(email, { otp, expires: Date.now() + 300000 }); // 5 minutes

  const mailOptions = {
    from: process.env.EMAIL_USER,
    to: email,
    subject: 'Trading Bot - Email Verification OTP',
    html: `
      <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #b19cd9;">Trading Bot Verification</h2>
        <p>Your OTP for email verification is:</p>
        <div style="background: #f0f0f0; padding: 20px; text-align: center; font-size: 24px; font-weight: bold; color: #333; border-radius: 10px;">
          ${otp}
        </div>
        <p>This OTP will expire in 5 minutes.</p>
        <p style="color: #666;">If you didn't request this, please ignore this email.</p>
      </div>
    `
  };

  try {
    await transporter.sendMail(mailOptions);
    res.json({ success: true, message: 'OTP sent successfully' });
  } catch (error) {
    console.error('Email error:', error);
    res.status(500).json({ error: 'Failed to send email' });
  }
});

// Verify OTP endpoint
app.post('/api/verify-otp', (req, res) => {
  const { email, otp } = req.body;
  
  const stored = otpStore.get(email);
  if (!stored) {
    return res.status(400).json({ error: 'OTP not found or expired' });
  }

  if (Date.now() > stored.expires) {
    otpStore.delete(email);
    return res.status(400).json({ error: 'OTP expired' });
  }

  if (stored.otp.toString() !== otp.toString()) {
    return res.status(400).json({ error: 'Invalid OTP' });
  }

  otpStore.delete(email);
  res.json({ success: true, message: 'Email verified successfully' });
});

// Signup endpoint
app.post('/api/signup', (req, res) => {
  const { firstName, lastName, email, mobile, password, aadhar } = req.body;

  // Check if Aadhar already exists
  if (users.some(user => user.aadhar === aadhar)) {
    return res.status(400).json({ error: 'Aadhar number already exists' });
  }

  // Validate Aadhar (12 digits only)
  if (!/^\d{12}$/.test(aadhar)) {
    return res.status(400).json({ error: 'Aadhar must be exactly 12 digits' });
  }

  // Validate password
  const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,15}$/;
  if (!passwordRegex.test(password) || password.includes(' ')) {
    return res.status(400).json({ error: 'Password must be 8-15 characters with uppercase, lowercase, number, and special character' });
  }

  users.push({ firstName, lastName, email, mobile, password, aadhar });
  res.json({ success: true, message: 'Signup successful' });
});

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});