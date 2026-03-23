"""
Audit Logger for SEBI Compliance

Maintains comprehensive audit trail of all trading decisions.
Required by SEBI for algorithmic trading.

Logs:
- Entry/Exit decisions and reasons
- Trade parameters
- Algo version
- Timestamps

Retention: 5 years (SEBI requirement)
"""

import json
from datetime import datetime
from typing import Dict, Optional


class AuditLogger:
    """
    Comprehensive audit logging for SEBI compliance.
    All trading decisions are logged with full context.
    """
    
    AUDIT_FILE = "audit_log.jsonl"  # JSON Lines format (append-only)
    ALGO_VERSION = "V47.14"
    
    @staticmethod
    async def log_decision(
        decision_type: str,
        reason: str,
        parameters: Dict,
        additional_context: Optional[Dict] = None
    ):
        """
        Log a trading decision for audit trail.
        
        Args:
            decision_type: Type of decision (ENTRY, EXIT, SKIP, STOP)
            reason: Human-readable reason for the decision
            parameters: Trade parameters used
            additional_context: Any additional context
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "decision_type": decision_type,
            "reason": reason,
            "parameters": parameters,
            "additional_context": additional_context or {},
            "algo_version": AuditLogger.ALGO_VERSION
        }
        
        try:
            # Append to audit log (JSON Lines format)
            with open(AuditLogger.AUDIT_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"⚠️ Audit logging failed: {e}")
    
    @staticmethod
    def get_recent_logs(count: int = 100) -> list:
        """Get recent audit log entries"""
        try:
            with open(AuditLogger.AUDIT_FILE, "r") as f:
                lines = f.readlines()
                return [json.loads(line) for line in lines[-count:]]
        except FileNotFoundError:
            return []
        except Exception as e:
            print(f"⚠️ Failed to read audit log: {e}")
            return []


# Global instance
audit_logger = AuditLogger()
