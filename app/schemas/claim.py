from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClaimBatchAnalyze(BaseModel):
    claim_ids: list[str] = Field(min_length=1, max_length=100)


class ClaimRead(BaseModel):
    id: str
    patient_name: str
    insurer: str
    cpt_code: str
    denial_code: str
    denial_reason: str
    auth_number_in_chart: str | None = None
    modifier_attached: str | None = None
    diagnosis_code: str | None = None
    eob_notes: str | None = None
    amount: float
    date_of_service: date
    status: Literal["PENDING", "ANALYZED", "RESOLVED"]
    suggested_fix: str | None = None
    policy_citation: str | None = None
    policy_confidence: float | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
