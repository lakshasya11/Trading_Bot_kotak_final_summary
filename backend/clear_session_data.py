#!/usr/bin/env python3
"""
Script to clear all user session data from the bot_sessions table
"""

from sqlalchemy import text
from core.database import today_engine

def clear_session_data():
    """Clear all data from bot_sessions table"""
    try:
        with today_engine.connect() as conn:
            # Get count before deletion
            count_result = conn.execute(text("SELECT COUNT(*) FROM bot_sessions"))
            total_records = count_result.fetchone()[0]
            
            if total_records == 0:
                print("✅ No session data found. Table is already empty.")
                return
            
            print(f"📊 Found {total_records} session records")
            
            # Confirm deletion
            confirm = input(f"⚠️  Are you sure you want to delete all {total_records} session records? (yes/no): ")
            
            if confirm.lower() in ['yes', 'y']:
                # Delete all records
                result = conn.execute(text("DELETE FROM bot_sessions"))
                conn.commit()
                
                print(f"✅ Successfully deleted {result.rowcount} session records")
                print("🧹 User session data table cleared!")
                
                # Reset auto-increment counter (optional)
                conn.execute(text("ALTER SEQUENCE bot_sessions_id_seq RESTART WITH 1"))
                conn.commit()
                print("🔄 Reset ID sequence to start from 1")
                
            else:
                print("❌ Operation cancelled. No data was deleted.")
                
    except Exception as e:
        print(f"❌ Error clearing session data: {e}")

if __name__ == "__main__":
    print("🗂️  User Session Data Cleaner")
    print("=" * 40)
    clear_session_data()
