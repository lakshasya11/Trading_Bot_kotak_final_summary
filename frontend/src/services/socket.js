// frontend/src/services/socket.js

// This function now simply creates and returns a configured socket instance.
// The management of this instance will be handled by the React component.


export const createSocketConnection = (onOpen, onMessage, onClose, onError) => {
    const MASTER_URL = import.meta.env.VITE_MASTER_BACKEND_URL;
    const WS_URL = MASTER_URL 
        ? MASTER_URL.replace(/^http/, 'ws') // Changes http:// to ws://
        : import.meta.env.VITE_API_WS_URL;

    const socket = new WebSocket(`${WS_URL}/ws`);

    socket.onopen = (event) => {
        console.log("WebSocket connected");
        onOpen(event);
    };

    socket.onmessage = (event) => {
        onMessage(event);
    };

    socket.onclose = (event) => {
        console.log("WebSocket disconnected");
        onClose(event);
    };

    socket.onerror = (error) => {
        console.error("WebSocket error:", error);
        onError(error);
    };

    return socket;
};