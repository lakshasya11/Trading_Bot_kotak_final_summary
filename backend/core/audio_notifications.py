"""
Professional Audio Notification System for Trading Bot
Plays distinct sounds for different trading events
"""
import os
import platform
import threading
from pathlib import Path

class AudioNotifier:
    """Professional audio notification system with system beeps"""
    
    def __init__(self):
        self.enabled = True
        self.system = platform.system()
        
    def _play_beep_sequence(self, frequencies, durations):
        """Play a sequence of beeps with specified frequencies and durations"""
        if not self.enabled:
            return
            
        def play():
            try:
                if self.system == "Windows":
                    import winsound
                    for freq, duration in zip(frequencies, durations):
                        if freq > 0:
                            winsound.Beep(freq, duration)
                        else:
                            # Silence/pause
                            import time
                            time.sleep(duration / 1000.0)
                elif self.system in ["Linux", "Darwin"]:  # Darwin is macOS
                    # Use system beep for Linux/Mac
                    for _ in range(len(frequencies)):
                        print('\a', end='', flush=True)
                        import time
                        time.sleep(0.2)
            except Exception as e:
                print(f"[AUDIO] Could not play sound: {e}")
        
        # Play in background thread so it doesn't block
        thread = threading.Thread(target=play, daemon=True)
        thread.start()
    
    def bot_started(self):
        """
        Bot Started Sound - Ascending professional chime
        Happy, welcoming, professional startup
        """
        # C4 -> E4 -> G4 -> C5 (Major chord progression - uplifting)
        frequencies = [523, 659, 784, 1047]
        durations = [150, 150, 150, 300]
        self._play_beep_sequence(frequencies, durations)
        print("🔊 [AUDIO] Bot started notification")
    
    def trade_entry(self, direction=""):
        """
        Trade Entry Sound - Confident double beep
        Professional, alert, confident
        """
        if direction.upper() == "CALL":
            # Higher pitch for CALL - G5 -> G5 (confident, higher register)
            frequencies = [1568, 0, 1568]
            durations = [120, 50, 200]
        elif direction.upper() == "PUT":
            # Lower pitch for PUT - C4 -> C4 (confident, lower register)
            frequencies = [523, 0, 523]
            durations = [120, 50, 200]
        else:
            # Neutral - E4 -> E4
            frequencies = [659, 0, 659]
            durations = [120, 50, 200]
        
        self._play_beep_sequence(frequencies, durations)
        print(f"🔊 [AUDIO] Trade entry notification ({direction})")
    
    def trade_exit(self, profit=True):
        """
        Trade Exit Sound - Distinct completion signal
        Different for profit vs loss
        """
        if profit:
            # Profit - Ascending happy notes (C5 -> E5 -> G5)
            frequencies = [1047, 1319, 1568]
            durations = [100, 100, 250]
            print("🔊 [AUDIO] Trade exit notification (PROFIT)")
        else:
            # Loss - Descending notes (G4 -> E4 -> C4)
            frequencies = [784, 659, 523]
            durations = [100, 100, 250]
            print("🔊 [AUDIO] Trade exit notification (LOSS)")
        
        self._play_beep_sequence(frequencies, durations)
    
    def bot_stopped(self):
        """
        Bot Stopped Sound - Descending professional chime
        Calm, professional shutdown
        """
        # C5 -> G4 -> E4 -> C4 (Descending - calm shutdown)
        frequencies = [1047, 784, 659, 523]
        durations = [150, 150, 150, 300]
        self._play_beep_sequence(frequencies, durations)
        print("🔊 [AUDIO] Bot stopped notification")
    
    def error_alert(self):
        """
        Error Alert - Urgent but not annoying
        Professional warning sound
        """
        # Alternating beeps - attention grabbing but professional
        frequencies = [800, 600, 800, 600]
        durations = [100, 100, 100, 200]
        self._play_beep_sequence(frequencies, durations)
        print("🔊 [AUDIO] Error alert notification")
    
    def target_hit(self):
        """
        Target Hit - Celebration sound
        Rewarding, positive feedback
        """
        # Major chord arpeggio - C5 -> E5 -> G5 -> C6
        frequencies = [1047, 1319, 1568, 2093]
        durations = [100, 100, 100, 300]
        self._play_beep_sequence(frequencies, durations)
        print("🔊 [AUDIO] Target hit notification")
    
    def stoploss_hit(self):
        """
        Stoploss Hit - Alert but not panic
        Professional warning
        """
        # Descending alert - F4 -> D4 -> A3
        frequencies = [698, 587, 440]
        durations = [150, 150, 250]
        self._play_beep_sequence(frequencies, durations)
        print("🔊 [AUDIO] Stoploss hit notification")
    
    def disable(self):
        """Disable all audio notifications"""
        self.enabled = False
        print("[AUDIO] Notifications disabled")
    
    def enable(self):
        """Enable audio notifications"""
        self.enabled = True
        print("[AUDIO] Notifications enabled")


# Global instance
audio_notifier = AudioNotifier()


# Quick test if run directly
if __name__ == "__main__":
    import time
    
    print("\n=== Testing Professional Audio Notifications ===\n")
    
    print("1. Bot Started:")
    audio_notifier.bot_started()
    time.sleep(2)
    
    print("\n2. Trade Entry (CALL):")
    audio_notifier.trade_entry("CALL")
    time.sleep(2)
    
    print("\n3. Trade Entry (PUT):")
    audio_notifier.trade_entry("PUT")
    time.sleep(2)
    
    print("\n4. Trade Exit (PROFIT):")
    audio_notifier.trade_exit(profit=True)
    time.sleep(2)
    
    print("\n5. Trade Exit (LOSS):")
    audio_notifier.trade_exit(profit=False)
    time.sleep(2)
    
    print("\n6. Target Hit:")
    audio_notifier.target_hit()
    time.sleep(2)
    
    print("\n7. Stoploss Hit:")
    audio_notifier.stoploss_hit()
    time.sleep(2)
    
    print("\n8. Error Alert:")
    audio_notifier.error_alert()
    time.sleep(2)
    
    print("\n9. Bot Stopped:")
    audio_notifier.bot_stopped()
    
    print("\n=== Test Complete ===\n")
