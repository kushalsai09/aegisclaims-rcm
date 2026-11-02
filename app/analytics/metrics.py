"""Database-backed financial and denial trend aggregations."""

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.claim import Claim


ACTIVE_STATUSES = ("PENDING", "ANALYZED")


class PayerMetric(BaseModel):
    insurer: str
    payer_key: str
    claim_count: int
    total_revenue: float
    at_risk_revenue: float
    recovered_revenue: float
    resolved_claims: int
    resolution_success_rate: float
    top_denial_code: str | None = None
    top_denial_reason: str | None = None
    top_denial_count: int = 0


class DenialCodeMetric(BaseModel):
    denial_code: str
    claim_count: int
    total_revenue: float
    resolved_claims: int
    resolution_success_rate: float


class PayerDenialTrend(BaseModel):
    insurer: str
    payer_key: str
    denial_code: str
    denial_reason: str
    claim_count: int
    revenue: float


class RecoveryTrend(BaseModel):
    period: str
    claim_count: int
    billed_revenue: float
    recovered_revenue: float
    at_risk_revenue: float


class AnalyticsSummary(BaseModel):
    generated_at: datetime
    total_claims: int
    pending_claims: int
    analyzed_claims: int
    resolved_claims: int
    total_at_risk_revenue: float
    total_recovered_revenue: float
    total_billed_revenue: float
    ai_analyzed_claims: int = Field(description="Claims that reached ANALYZED or RESOLVED status.")
    ai_resolution_rate: float = Field(description="Resolved share of claims that reached AI analysis.")
    first_pass_resolution_rate: float
    automation_coverage_rate: float
    average_claim_value: float
    payer_breakdown: list[PayerMetric]
    top_denial_codes: list[DenialCodeMetric]
    payer_denial_trends: list[PayerDenialTrend]
    recovery_trends: list[RecoveryTrend]


def get_analytics_summary(db: Session) -> AnalyticsSummary:
    """Aggregate executive RCM metrics using SQL rather than loading claim rows."""

    status_totals = db.execute(
        select(
            func.count(Claim.id),
            func.coalesce(func.sum(case((Claim.status == "PENDING", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Claim.status == "ANALYZED", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Claim.status == "RESOLVED", 1), else_=0)), 0),
            func.coalesce(
                func.sum(case((Claim.status.in_(ACTIVE_STATUSES), Claim.amount), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Claim.status == "RESOLVED", Claim.amount), else_=0)), 0
            ),
            func.coalesce(func.sum(Claim.amount), 0),
        )
    ).one()

    total_claims = int(status_totals[0])
    pending_claims = int(status_totals[1])
    analyzed_claims = int(status_totals[2])
    resolved_claims = int(status_totals[3])
    ai_analyzed_claims = analyzed_claims + resolved_claims

    trend_rows = db.execute(
        select(
            Claim.insurer,
            Claim.denial_code,
            Claim.denial_reason,
            func.count(Claim.id).label("claim_count"),
            func.coalesce(func.sum(Claim.amount), 0).label("revenue"),
        )
        .group_by(Claim.insurer, Claim.denial_code, Claim.denial_reason)
        .order_by(func.count(Claim.id).desc(), func.sum(Claim.amount).desc())
    ).all()
    trends = [
        PayerDenialTrend(
            insurer=row.insurer,
            payer_key=_payer_key(row.insurer),
            denial_code=row.denial_code,
            denial_reason=row.denial_reason,
            claim_count=int(row.claim_count),
            revenue=_money(row.revenue),
        )
        for row in trend_rows
    ]

    top_trend_by_payer: dict[str, PayerDenialTrend] = {}
    for trend in trends:
        top_trend_by_payer.setdefault(trend.insurer, trend)

    payer_rows = db.execute(
        select(
            Claim.insurer,
            func.count(Claim.id).label("claim_count"),
            func.coalesce(func.sum(Claim.amount), 0).label("total_revenue"),
            func.coalesce(
                func.sum(case((Claim.status.in_(ACTIVE_STATUSES), Claim.amount), else_=0)), 0
            ).label("at_risk_revenue"),
            func.coalesce(
                func.sum(case((Claim.status == "RESOLVED", Claim.amount), else_=0)), 0
            ).label("recovered_revenue"),
            func.coalesce(func.sum(case((Claim.status == "RESOLVED", 1), else_=0)), 0).label(
                "resolved_claims"
            ),
        )
        .group_by(Claim.insurer)
        .order_by(func.sum(Claim.amount).desc())
    ).all()
    payer_breakdown = []
    for row in payer_rows:
        top_trend = top_trend_by_payer.get(row.insurer)
        payer_breakdown.append(
            PayerMetric(
                insurer=row.insurer,
                payer_key=_payer_key(row.insurer),
                claim_count=int(row.claim_count),
                total_revenue=_money(row.total_revenue),
                at_risk_revenue=_money(row.at_risk_revenue),
                recovered_revenue=_money(row.recovered_revenue),
                resolved_claims=int(row.resolved_claims),
                resolution_success_rate=_rate(int(row.resolved_claims), int(row.claim_count)),
                top_denial_code=top_trend.denial_code if top_trend else None,
                top_denial_reason=top_trend.denial_reason if top_trend else None,
                top_denial_count=top_trend.claim_count if top_trend else 0,
            )
        )

    denial_rows = db.execute(
        select(
            Claim.denial_code,
            func.count(Claim.id).label("claim_count"),
            func.coalesce(func.sum(Claim.amount), 0).label("total_revenue"),
            func.coalesce(func.sum(case((Claim.status == "RESOLVED", 1), else_=0)), 0).label(
                "resolved_claims"
            ),
        )
        .group_by(Claim.denial_code)
        .order_by(func.count(Claim.id).desc(), func.sum(Claim.amount).desc())
    ).all()
    denial_codes = [
        DenialCodeMetric(
            denial_code=row.denial_code,
            claim_count=int(row.claim_count),
            total_revenue=_money(row.total_revenue),
            resolved_claims=int(row.resolved_claims),
            resolution_success_rate=_rate(int(row.resolved_claims), int(row.claim_count)),
        )
        for row in denial_rows
    ]

    recovery_rows = db.execute(
        select(
            Claim.date_of_service.label("period"),
            func.count(Claim.id).label("claim_count"),
            func.coalesce(func.sum(Claim.amount), 0).label("billed_revenue"),
            func.coalesce(
                func.sum(case((Claim.status == "RESOLVED", Claim.amount), else_=0)), 0
            ).label("recovered_revenue"),
            func.coalesce(
                func.sum(case((Claim.status.in_(ACTIVE_STATUSES), Claim.amount), else_=0)), 0
            ).label("at_risk_revenue"),
        )
        .group_by(Claim.date_of_service)
        .order_by(Claim.date_of_service)
    ).all()
    recovery_trends = [
        RecoveryTrend(
            period=row.period.isoformat(),
            claim_count=int(row.claim_count),
            billed_revenue=_money(row.billed_revenue),
            recovered_revenue=_money(row.recovered_revenue),
            at_risk_revenue=_money(row.at_risk_revenue),
        )
        for row in recovery_rows
    ]

    return AnalyticsSummary(
        generated_at=datetime.now(timezone.utc),
        total_claims=total_claims,
        pending_claims=pending_claims,
        analyzed_claims=analyzed_claims,
        resolved_claims=resolved_claims,
        total_at_risk_revenue=_money(status_totals[4]),
        total_recovered_revenue=_money(status_totals[5]),
        total_billed_revenue=_money(status_totals[6]),
        ai_analyzed_claims=ai_analyzed_claims,
        ai_resolution_rate=_rate(resolved_claims, ai_analyzed_claims),
        first_pass_resolution_rate=_rate(resolved_claims, total_claims),
        automation_coverage_rate=_rate(ai_analyzed_claims, total_claims),
        average_claim_value=_money(status_totals[6] / total_claims) if total_claims else 0.0,
        payer_breakdown=payer_breakdown,
        top_denial_codes=denial_codes,
        payer_denial_trends=trends,
        recovery_trends=recovery_trends,
    )


def _money(value: Decimal | int | float) -> float:
    return round(float(value), 2)


def _rate(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 2) if denominator else 0.0


def _payer_key(insurer: str) -> str:
    normalized = insurer.lower()
    if "blue cross" in normalized or "blue shield" in normalized or normalized == "bcbs":
        return "BCBS"
    if "aetna" in normalized:
        return "Aetna"
    if "cigna" in normalized:
        return "Cigna"
    if "unitedhealth" in normalized or normalized == "uhc":
        return "UHC"
    return insurer
