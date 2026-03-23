import React from 'react';

class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null, errorInfo: null };
    }

    static getDerivedStateFromError(error) {
        return { hasError: true };
    }

    componentDidCatch(error, errorInfo) {
        console.error('❌ ErrorBoundary caught:', error, errorInfo);
        this.setState({ error, errorInfo });
    }

    render() {
        if (this.state.hasError) {
            return (
                <div style={{ padding: '20px', backgroundColor: '#ffebee', border: '2px solid #c62828', margin: '10px', borderRadius: '8px' }}>
                    <h2 style={{ color: '#c62828' }}>❌ {this.props.name || 'Component'} Error</h2>
                    <p><strong>Error:</strong> {this.state.error?.toString()}</p>
                    <details style={{ marginTop: '10px' }}>
                        <summary style={{ cursor: 'pointer', fontWeight: 'bold' }}>Show Details</summary>
                        <pre style={{ backgroundColor: '#fff', padding: '10px', overflow: 'auto', fontSize: '12px' }}>
                            {this.state.errorInfo?.componentStack}
                        </pre>
                    </details>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
