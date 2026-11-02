from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.async_api import Error as PlaywrightError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.analytics.metrics import AnalyticsSummary, get_analytics_summary
from app.config import settings
from app.database import get_db, initialize_database
from app.models.audit import AuditLog
from app.models.claim import Claim
from app.rag.resolver import resolve_denial
from app.rag.vector_store import get_policy_collection, index_policies, list_policy_chunks, search_policies
from app.schemas.audit import AuditLogRead
from app.schemas.claim import ClaimBatchAnalyze, ClaimRead
from app.scraper.portal_scraper import PORTALS, scrape_all_portals
from app.services.audit_service import record_audit_log
from app.services.pdf_generator import generate_appeal_pdf


APP_PATH = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_PATH / "templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    index_policies()
    yield


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_PATH / "static"), name="static")
DbSession = Annotated[Session, Depends(get_db)]


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
@app.get("/analytics", response_class=HTMLResponse, include_in_schema=False)
@app.get("/rag", response_class=HTMLResponse, include_in_schema=False)
@app.get("/integrations", response_class=HTMLResponse, include_in_schema=False)
@app.get("/audit", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="dashboard.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/claims", response_model=list[ClaimRead])
def list_claims(db: DbSession) -> list[Claim]:
    statement = select(Claim).order_by(Claim.created_at.desc(), Claim.id)
    return list(db.scalars(statement).all())


@app.get("/api/analytics/summary", response_model=AnalyticsSummary)
def analytics_summary(db: DbSession) -> AnalyticsSummary:
    return get_analytics_summary(db)


@app.get("/api/rag/chunks")
def rag_chunks(
    q: str | None = Query(default=None, max_length=500),
    payer: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    if q and q.strip():
        matches = search_policies(query=q.strip(), payer=payer, limit=limit)
        chunks: list[dict[str, object]] = [
            {
                "id": f"search-{index}",
                "text": match.text,
                "payer": match.payer,
                "policy_title": match.policy_title,
                "section": match.section,
                "source": match.source,
                "distance": round(match.distance, 6),
                "match_score": round(max(0.0, min(100.0, (1.0 - match.distance) * 100.0)), 2),
            }
            for index, match in enumerate(matches)
        ]
    else:
        chunks = list_policy_chunks(limit=limit)
        if payer:
            chunks = [chunk for chunk in chunks if str(chunk["payer"]).lower() == payer.lower()]

    manuals = sorted(
        {
            (str(chunk["source"]), str(chunk["policy_title"]), str(chunk["payer"]))
            for chunk in chunks
        }
    )
    return {
        "total_indexed": get_policy_collection().count(),
        "query": q.strip() if q else None,
        "manuals": [
            {"source": source, "policy_title": title, "payer": manual_payer}
            for source, title, manual_payer in manuals
        ],
        "chunks": chunks,
    }


@app.get("/api/audit-logs", response_model=list[AuditLogRead])
def all_audit_logs(
    db: DbSession,
    q: str | None = Query(default=None, max_length=200),
    action: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[AuditLog]:
    statement = select(AuditLog)
    if q and q.strip():
        term = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                AuditLog.claim_id.ilike(term),
                AuditLog.details.ilike(term),
                AuditLog.performed_by.ilike(term),
            )
        )
    if action:
        statement = statement.where(AuditLog.action == action.upper())
    statement = statement.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(limit)
    return list(db.scalars(statement).all())


@app.get("/api/integrations/status")
def integration_status() -> dict[str, object]:
    portals = [
        {
            "id": portal.payer.lower().replace(" ", "-"),
            "name": (
                "BCBS Portal"
                if portal.payer == "Blue Cross Blue Shield"
                else "Aetna Gateway"
                if portal.payer == "Aetna"
                else "UHC Portal"
                if portal.payer == "UnitedHealthcare"
                else f"{portal.payer} Portal"
            ),
            "status": (
                "syncing"
                if portal.payer == "UnitedHealthcare" and portal.path.exists()
                else "online"
                if portal.path.exists()
                else "degraded"
            ),
            "transport": "Playwright / secure portal",
            "last_check": datetime.now(timezone.utc).isoformat(),
        }
        for portal in PORTALS
    ]
    return {
        "environment": settings.app_environment,
        "scheduler": {"status": "active", "cadence": "Every 30 minutes"},
        "vector_store": {
            "status": "online",
            "collection": "payer_policies",
            "indexed_chunks": get_policy_collection().count(),
        },
        "portals": portals,
    }


@app.post(
    "/api/scraper/run",
    response_model=list[ClaimRead],
    status_code=status.HTTP_200_OK,
)
async def run_scraper() -> list[Claim]:
    try:
        return await scrape_all_portals()
    except PlaywrightError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The scraper browser could not start. Run 'playwright install chromium'.",
        ) from exc


@app.post("/api/claims/{claim_id}/analyze", response_model=ClaimRead)
def analyze_claim(claim_id: str, db: DbSession) -> Claim:
    claim = _get_claim_or_404(db, claim_id)

    _apply_analysis(claim, db)
    db.commit()
    db.refresh(claim)
    return claim


@app.post("/api/claims/analyze-all", response_model=list[ClaimRead])
def analyze_all_claims(db: DbSession) -> list[Claim]:
    claims = list(
        db.scalars(
            select(Claim).where(Claim.status == "PENDING").order_by(Claim.created_at, Claim.id)
        ).all()
    )
    try:
        for claim in claims:
            _apply_analysis(claim, db)
        db.commit()
    except HTTPException:
        db.rollback()
        raise

    for claim in claims:
        db.refresh(claim)
    return claims


@app.post("/api/claims/analyze-batch", response_model=list[ClaimRead])
def analyze_claim_batch(payload: ClaimBatchAnalyze, db: DbSession) -> list[Claim]:
    claim_ids = list(dict.fromkeys(payload.claim_ids))
    claims_by_id = {
        claim.id: claim
        for claim in db.scalars(select(Claim).where(Claim.id.in_(claim_ids))).all()
    }
    missing = [claim_id for claim_id in claim_ids if claim_id not in claims_by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Claims not found: {', '.join(missing)}",
        )
    claims = [claims_by_id[claim_id] for claim_id in claim_ids if claims_by_id[claim_id].status != "RESOLVED"]
    if not claims:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No selected claims are eligible for analysis.",
        )
    try:
        for claim in claims:
            _apply_analysis(claim, db)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    for claim in claims:
        db.refresh(claim)
    return claims


@app.post("/api/claims/{claim_id}/resolve", response_model=ClaimRead)
def resolve_claim(claim_id: str, db: DbSession) -> Claim:
    claim = _get_claim_or_404(db, claim_id)
    if not claim.suggested_fix:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analyze the claim before approving it for resubmission.",
        )

    if claim.status != "RESOLVED":
        claim.status = "RESOLVED"
        record_audit_log(
            db,
            claim_id=claim.id,
            action="CLAIM_RESUBMITTED",
            details="Claim approved, resubmitted to the clearinghouse, and moved to recovered revenue.",
            performed_by="Billing Operations",
        )
    db.commit()
    db.refresh(claim)
    return claim


@app.get("/api/claims/{claim_id}/audit-logs", response_model=list[AuditLogRead])
def claim_audit_logs(claim_id: str, db: DbSession) -> list[AuditLog]:
    _get_claim_or_404(db, claim_id)
    statement = (
        select(AuditLog)
        .where(AuditLog.claim_id == claim_id)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    )
    return list(db.scalars(statement).all())


@app.get("/api/claims/{claim_id}/export/clearinghouse")
def export_clearinghouse(claim_id: str, db: DbSession) -> dict[str, object]:
    claim = _get_analyzed_claim_or_409(db, claim_id)
    exported_at = datetime.now(timezone.utc)
    payload: dict[str, object] = {
        "format": "CMS-1500/837P-ready JSON",
        "version": "1.0",
        "transaction_type": "PROFESSIONAL_CORRECTED_CLAIM",
        "exported_at": exported_at.isoformat(),
        "submitter": {
            "organization_name": "MainStreet Family Care",
            "role": "Billing Provider",
        },
        "receiver": {"payer_name": claim.insurer},
        "claim": {
            "patient_control_number": claim.id,
            "payer_claim_control_number": claim.id,
            "claim_frequency_code": "7",
            "patient": {"name": claim.patient_name},
            "date_of_service": claim.date_of_service.isoformat(),
            "total_charge": float(claim.amount),
            "currency": "USD",
            "service_lines": [
                {
                    "line_number": 1,
                    "procedure_code": claim.cpt_code,
                    "charge_amount": float(claim.amount),
                    "units": 1,
                }
            ],
            "original_denial": {
                "code": claim.denial_code,
                "reason": claim.denial_reason,
            },
            "corrective_action": claim.suggested_fix,
            "policy_support": claim.policy_citation,
        },
    }
    record_audit_log(
        db,
        claim_id=claim.id,
        action="EXPORTED",
        details="Generated CMS-1500/837P-ready clearinghouse JSON payload.",
        performed_by="Billing Specialist",
    )
    db.commit()
    return payload


@app.get("/api/claims/{claim_id}/export/appeal-pdf")
def export_appeal_pdf(claim_id: str, db: DbSession) -> StreamingResponse:
    claim = _get_analyzed_claim_or_409(db, claim_id)
    try:
        pdf_bytes = generate_appeal_pdf(claim)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    record_audit_log(
        db,
        claim_id=claim.id,
        action="EXPORTED",
        details="Generated one-page payer appeal summary PDF.",
        performed_by="Billing Specialist",
    )
    db.commit()
    filename = f"MainStreet_Appeal_{claim.id}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _apply_analysis(claim: Claim, db: Session) -> None:
    try:
        resolution = resolve_denial(
            denial_code=claim.denial_code,
            denial_reason=claim.denial_reason,
            cpt_code=claim.cpt_code,
            insurer=claim.insurer,
            auth_number_in_chart=claim.auth_number_in_chart,
            modifier_attached=claim.modifier_attached,
            diagnosis_code=claim.diagnosis_code,
            eob_notes=claim.eob_notes,
        )
    except (LookupError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Policy resolution is temporarily unavailable.",
        ) from exc

    claim.suggested_fix = resolution.suggested_fix
    claim.policy_citation = resolution.policy_citation
    claim.policy_confidence = resolution.policy_confidence
    if claim.status != "RESOLVED":
        claim.status = "ANALYZED"
    record_audit_log(
        db,
        claim_id=claim.id,
        action="ANALYZED",
        details=f"RAG policy analysis completed for denial {claim.denial_code}.",
        performed_by="System",
    )


def _get_claim_or_404(db: Session, claim_id: str) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Claim '{claim_id}' was not found.",
        )
    return claim


def _get_analyzed_claim_or_409(db: Session, claim_id: str) -> Claim:
    claim = _get_claim_or_404(db, claim_id)
    if not claim.suggested_fix or not claim.policy_citation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analyze the claim before exporting it.",
        )
    return claim
