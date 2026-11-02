from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AuditLogRead(BaseModel):
    id: int
    claim_id: str
    action: Literal[
        "SCRAPED",
        "ANALYZED",
        "RESOLVED",
        "CLAIM_RESUBMITTED",
        "EXPORTED",
    ]
    details: str
    performed_by: str
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)
