// Diagnostic tool to check dashboard data flow

export async function runDiagnostics() {
    console.log('\n' + '='.repeat(60));
    console.log('🔍 DASHBOARD DATA DIAGNOSTICS');
    console.log('='.repeat(60) + '\n');

    // 1. Check Store
    console.log('1️⃣ CHECKING ZUSTAND STORE...');
    try {
        const { useStore } = await import('../store/store.js');
        const store = useStore.getState();
        console.log('   ✅ Store accessible');
        console.log(`   📊 tradeHistory length: ${store.tradeHistory?.length || 0}`);
        console.log(`   📊 allTimeTradeHistory length: ${store.allTimeTradeHistory?.length || 0}`);
        console.log(`   📊 dailyPerformance.trades_today: ${store.dailyPerformance?.trades_today || 0}`);
        
        if (store.tradeHistory?.length > 0) {
            console.log(`   📋 First trade: ${store.tradeHistory[0].symbol}`);
        }
    } catch (error) {
        console.error('   ❌ Store error:', error);
    }

    // 2. Check localStorage
    console.log('\n2️⃣ CHECKING LOCALSTORAGE...');
    try {
        const savedToday = JSON.parse(localStorage.getItem('tradeHistory') || '[]');
        const savedAllTime = JSON.parse(localStorage.getItem('allTimeTradeHistory') || '[]');
        console.log(`   📦 localStorage tradeHistory: ${savedToday.length} trades`);
        console.log(`   📦 localStorage allTimeTradeHistory: ${savedAllTime.length} trades`);
        
        if (savedToday.length === 0 && savedAllTime.length === 0) {
            console.warn('   ⚠️ localStorage is empty - data not persisting');
        }
    } catch (error) {
        console.error('   ❌ localStorage error:', error);
    }

    // 3. Check API
    console.log('\n3️⃣ CHECKING API ENDPOINTS...');
    try {
        const todayRes = await fetch('http://localhost:8000/api/trade_history');
        const todayData = await todayRes.json();
        console.log(`   🌐 /api/trade_history: ${todayData.length} trades`);
        
        const allTimeRes = await fetch('http://localhost:8000/api/trade_history_all');
        const allTimeData = await allTimeRes.json();
        console.log(`   🌐 /api/trade_history_all: ${allTimeData.length} trades`);
        
        if (todayData.length > 0) {
            console.log(`   📋 Sample trade: ${todayData[0].symbol} @ ₹${todayData[0].entry_price}`);
        }
    } catch (error) {
        console.error('   ❌ API error:', error);
    }

    // 4. Summary
    console.log('\n' + '='.repeat(60));
    console.log('📊 SUMMARY');
    console.log('='.repeat(60));
    console.log('If all numbers above are > 0, data is flowing correctly.');
    console.log('If any are 0, that\'s where the problem is.');
    console.log('\n');
}

// Run diagnostics
if (typeof window !== 'undefined') {
    window.runDiagnostics = runDiagnostics;
    console.log('💡 Run diagnostics with: runDiagnostics()');
}
