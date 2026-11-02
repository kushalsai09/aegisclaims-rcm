from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def record_audit_log(
    db: Session,
    *,
    claim_id: str,
    action: str,
    details: str,
    performed_by: str = "System",
) -> AuditLog:
    log = AuditLog(
        claim_id=claim_id,
        action=action,
        details=details,
        performed_by=performed_by,
    )
    db.add(log)
    return log
