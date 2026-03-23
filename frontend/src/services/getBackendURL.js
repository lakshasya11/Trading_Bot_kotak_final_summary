// Helper function to get backend URL
export function getBackendURL() {
    const masterURL = import.meta.env.VITE_MASTER_BACKEND_URL;
    const apiHttpURL = import.meta.env.VITE_API_HTTP_URL;
    
    if (masterURL) {
        return masterURL;
    }
    
    if (apiHttpURL) {
        return apiHttpURL;
    }
    
    // Default fallback
    return 'http://localhost:8000';
}
