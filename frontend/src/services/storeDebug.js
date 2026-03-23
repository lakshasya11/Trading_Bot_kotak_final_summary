/**
 * Store Debug Utility
 * Run in browser console: import('./services/storeDebug.js').then(m => m.debugStore())
 */

export async function debugStore() {
    const { useStore } = await import('../store/store.js');
    
    const state = useStore.getState();
    
    console.log('=== STORE DEBUG ===');
    console.log('tradeHistory length:', state.tradeHistory?.length || 0);
    console.log('allTimeTradeHistory length:', state.allTimeTradeHistory?.length || 0);
    console.log('tradeHistory sample:', state.tradeHistory?.slice(0, 2));
    console.log('allTimeTradeHistory sample:', state.allTimeTradeHistory?.slice(0, 2));
    
    console.log('\n=== LOCALSTORAGE DEBUG ===');
    const savedToday = localStorage.getItem('tradeHistory');
    const savedAllTime = localStorage.getItem('allTimeTradeHistory');
    
    console.log('localStorage tradeHistory:', savedToday ? JSON.parse(savedToday).length : 0, 'trades');
    console.log('localStorage allTimeTradeHistory:', savedAllTime ? JSON.parse(savedAllTime).length : 0, 'trades');
    
    console.log('\n=== API DEBUG ===');
    try {
        const todayRes = await fetch('http://localhost:8000/api/trade_history');
        const todayData = await todayRes.json();
        console.log('API /trade_history:', todayData.length, 'trades');
        console.log('Sample:', todayData.slice(0, 1));
    } catch (e) {
        console.error('API /trade_history failed:', e.message);
    }
    
    try {
        const allRes = await fetch('http://localhost:8000/api/trade_history_all');
        const allData = await allRes.json();
        console.log('API /trade_history_all:', allData.length, 'trades');
    } catch (e) {
        console.error('API /trade_history_all failed:', e.message);
    }
    
    console.log('=== END DEBUG ===');
}

// Also export a function to manually set trades for testing
export function setTestTrades() {
    const { useStore } = require('../store/store.js');
    const testTrades = [
        { id: 1, symbol: 'NIFTY23000CE', entry_price: 100, exit_price: 150, net_pnl: 50, pnl: 50, timestamp: new Date().toISOString() },
        { id: 2, symbol: 'NIFTY23100PE', entry_price: 200, exit_price: 180, net_pnl: -20, pnl: -20, timestamp: new Date().toISOString() }
    ];
    
    useStore.getState().setTradeHistory(testTrades);
    useStore.getState().setAllTimeTradeHistory(testTrades);
    
    console.log('✅ Test trades set. Store now has:', useStore.getState().tradeHistory.length, 'trades');
}
