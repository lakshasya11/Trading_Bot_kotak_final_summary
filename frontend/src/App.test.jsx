import React from 'react';

function AppTest() {
    return (
        <div style={{ 
            padding: '40px', 
            backgroundColor: '#fff', 
            minHeight: '100vh',
            fontFamily: 'Arial, sans-serif'
        }}>
            <h1 style={{ color: '#2e7d32', fontSize: '48px' }}>
                ✅ React is Working!
            </h1>
            <p style={{ fontSize: '24px', marginTop: '20px' }}>
                If you can see this, the frontend is rendering correctly.
            </p>
            <div style={{ 
                marginTop: '30px', 
                padding: '20px', 
                backgroundColor: '#e3f2fd',
                borderRadius: '8px'
            }}>
                <h2>Next Steps:</h2>
                <ol style={{ fontSize: '18px', lineHeight: '2' }}>
                    <li>React is loading ✅</li>
                    <li>Vite dev server is running ✅</li>
                    <li>Now let's test the full app...</li>
                </ol>
            </div>
        </div>
    );
}

export default AppTest;
