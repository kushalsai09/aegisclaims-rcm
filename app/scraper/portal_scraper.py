from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from playwright.async_api import Browser, async_playwright
from sqlalchemy.orm import Session

from app.database import SessionLocal, initialize_database
from app.models.claim import Claim
from app.services.audit_service import record_audit_log


PORTALS_PATH = Path(__file__).resolve().parent.parent / "mock_portals"


@dataclass(frozen=True)
class PortalConfig:
    payer: str
    path: Path


PORTALS = (
    PortalConfig(payer="Blue Cross Blue Shield", path=PORTALS_PATH / "bcbs_portal.html"),
    PortalConfig(payer="Aetna", path=PORTALS_PATH / "aetna_portal.html"),
    PortalConfig(payer="Cigna", path=PORTALS_PATH / "cigna_portal.html"),
    PortalConfig(payer="UnitedHealthcare", path=PORTALS_PATH / "uhc_portal.html"),
)


async def scrape_all_portals() -> list[Claim]:
    """Sequentially scrape every configured payer portal and upsert results atomically."""

    missing = [str(portal.path) for portal in PORTALS if not portal.path.exists()]
    if missing:
        raise FileNotFoundError(f"Mock portals not found: {', '.join(missing)}")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            scraped: list[tuple[PortalConfig, dict[str, str | Decimal | date | None]]] = []
            for portal in PORTALS:
                rows = await _scrape_portal(browser, portal)
                scraped.extend((portal, row) for row in rows)
        finally:
            await browser.close()

    initialize_database()
    with SessionLocal() as db:
        ingested: list[Claim] = []
        for portal, claim_data in scraped:
            claim, created = _upsert_claim(db, claim_data)
            ingested.append(claim)
            record_audit_log(
                db,
                claim_id=claim.id,
                action="SCRAPED",
                details=(
                    f"Claim {'created' if created else 'refreshed'} from the "
                    f"{portal.payer} mock payer portal."
                ),
                performed_by="System",
            )
        db.commit()
        for claim in ingested:
            db.refresh(claim)
        return ingested


async def scrape_mock_portal() -> list[Claim]:
    """Backward-compatible alias for the multi-payer scraper."""

    return await scrape_all_portals()


async def _scrape_portal(
    browser: Browser,
    portal: PortalConfig,
) -> list[dict[str, str | Decimal | date | None]]:
    page = await browser.new_page()
    try:
        await page.goto(portal.path.as_uri(), wait_until="domcontentloaded")
        rows = await page.locator("#denied-claims tbody tr").all()
        claims: list[dict[str, str | Decimal | date | None]] = []
        for row in rows:
            cells = await row.locator("td").all_inner_texts()
            if len(cells) != 8:
                continue
            try:
                service_date = date.fromisoformat(cells[3].strip())
                amount = Decimal(cells[6].strip().replace("$", "").replace(",", ""))
            except (ValueError, InvalidOperation):
                continue
            claims.append(
                {
                    "id": cells[0].strip(),
                    "patient_name": cells[1].strip(),
                    "insurer": portal.payer,
                    "date_of_service": service_date,
                    "cpt_code": cells[4].strip(),
                    "denial_code": cells[5].strip(),
                    "amount": amount,
                    "denial_reason": cells[7].strip(),
                    "auth_number_in_chart": _optional_attr(
                        await row.get_attribute("data-auth-number")
                    ),
                    "modifier_attached": _optional_attr(
                        await row.get_attribute("data-modifier")
                    ),
                    "diagnosis_code": _optional_attr(
                        await row.get_attribute("data-diagnosis-code")
                    ),
                    "eob_notes": _optional_attr(await row.get_attribute("data-eob-notes")),
                }
            )
        return claims
    finally:
        await page.close()


def _upsert_claim(
    db: Session,
    claim_data: dict[str, str | Decimal | date | None],
) -> tuple[Claim, bool]:
    claim_id = str(claim_data["id"])
    claim = db.get(Claim, claim_id)
    if claim is None:
        claim = Claim(**claim_data, status="PENDING")
        db.add(claim)
        db.flush()
        return claim, True

    for field, value in claim_data.items():
        setattr(claim, field, value)
    return claim, False


def _optional_attr(value: str | None) -> str | None:
    normalized = value.strip() if value else ""
    return normalized or None
