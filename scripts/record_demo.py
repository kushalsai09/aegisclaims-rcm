#!/usr/bin/env python3
"""Record the full-frame 2K MainStreet RCM demo for Motion.so."""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import Browser, BrowserContext, Locator, Page, sync_playwright


APP_URL = "http://localhost:8000/"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = PROJECT_ROOT / "recordings"
VIDEO_SIZE = {"width": 2560, "height": 1440}
DEVICE_SCALE_FACTOR = 2
UI_ZOOM = "1.3"

# Voiceover timing markers. Keep these explicit so the Motion.so timeline is stable.
HOLD_PENDING_MS = 4_000
HOLD_ANALYSIS_MS = 3_000
HOLD_RECOMMENDATION_MS = 8_000
HOLD_SUBMITTED_MS = 6_000
HOLD_KPI_MS = 3_000
HOLD_CHART_MS = 4_000
HOLD_POLICY_RESULTS_MS = 8_000
HOLD_AUDIT_FILTERED_MS = 6_000
WORKSPACE_RENDER_MS = 1_500
CONTROL_SETTLE_MS = 750
CURSOR_TRAVEL_SECONDS = 1.7

TARGET_CLAIM_ID = "MSC-10006"
TARGET_EXPOSURE = 2_842.75
DEMO_RESOLVED_IDS = {"BCBS-24001", "BCBS-24002", "CIGNA-41002", "UHC-51002"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
LOGGER = logging.getLogger("mainstreet-demo")


def hold(page: Page, milliseconds: int, label: str) -> None:
    """Keep the cursor completely still for a narratable timeline hold."""
    LOGGER.info("HOLD %4.1fs  %s", milliseconds / 1_000, label)
    page.wait_for_timeout(milliseconds)


def require_visible(locator: Locator, label: str, timeout_ms: int = 15_000) -> Locator:
    """Return one visible target or stop rather than silently recording the wrong UI."""
    locator.first.wait_for(state="visible", timeout=timeout_ms)
    LOGGER.info("TARGET       %s", label)
    return locator.first


def button(page: Page, *names: str) -> Locator:
    """Resolve the first visible button alias used by the current dashboard build."""
    for name in names:
        candidate = page.get_by_role("button", name=name, exact=False)
        if candidate.count() and candidate.first.is_visible():
            return candidate.first
    raise RuntimeError(f"No visible button matched: {', '.join(names)}")


class SmoothCursor:
    """Move the mouse with eased, human-paced 1.5–2 second transitions."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.x = VIDEO_SIZE["width"] * 0.82
        self.y = 120.0
        self.page.mouse.move(self.x, self.y, steps=25)

    def move_to(
        self,
        locator: Locator,
        *,
        duration_seconds: float = CURSOR_TRAVEL_SECONDS,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
        label: str,
    ) -> None:
        locator.scroll_into_view_if_needed(timeout=8_000)
        box = locator.bounding_box()
        if box is None:
            raise RuntimeError(f"Cannot determine cursor target geometry: {label}")
        target_x = box["x"] + box["width"] * x_ratio
        target_y = box["y"] + box["height"] * y_ratio
        self.move_to_point(target_x, target_y, duration_seconds=duration_seconds, label=label)

    def move_to_point(
        self,
        target_x: float,
        target_y: float,
        *,
        duration_seconds: float = CURSOR_TRAVEL_SECONDS,
        label: str,
    ) -> None:
        LOGGER.info("CURSOR %4.1fs  %s", duration_seconds, label)
        # Playwright emits exactly 25 interpolated pointer events. The settle
        # pause keeps the complete move within the narrated 1.5–2 second beat.
        self.page.mouse.move(target_x, target_y, steps=25)
        remaining_ms = max(int(duration_seconds * 1_000) - 400, 150)
        self.page.wait_for_timeout(remaining_ms)
        self.x, self.y = target_x, target_y

    def click(self, locator: Locator, *, label: str) -> None:
        self.move_to(locator, label=label)
        self.page.mouse.down()
        time.sleep(0.11)
        self.page.mouse.up()
        self.page.wait_for_timeout(250)

    def hover_and_hold(self, locator: Locator, milliseconds: int, *, label: str) -> None:
        self.move_to(locator, label=label)
        hold(self.page, milliseconds, label)


def prepare_demo_state() -> None:
    """Normalize the local prototype so every recording starts from the same frame."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from sqlalchemy import delete, select

    from app.database import SessionLocal, initialize_database
    from app.models.audit import AuditLog
    from app.models.claim import Claim

    initialize_database()
    with SessionLocal() as db:
        claims = list(db.scalars(select(Claim)).all())
        if not claims:
            raise RuntimeError("No claims are available. Run the seed and scraper before recording.")

        for claim in claims:
            claim.status = "RESOLVED" if claim.id in DEMO_RESOLVED_IDS else "ANALYZED"

        lucas = db.get(Claim, TARGET_CLAIM_ID)
        if lucas is None:
            raise RuntimeError(f"Required demo claim {TARGET_CLAIM_ID} was not found.")
        lucas.status = "PENDING"
        lucas.suggested_fix = None
        lucas.policy_citation = None
        lucas.policy_confidence = None

        db.execute(
            delete(AuditLog).where(
                AuditLog.claim_id == TARGET_CLAIM_ID,
                AuditLog.action == "CLAIM_RESUBMITTED",
            )
        )
        db.commit()


def fetch_json(path: str) -> dict[str, object]:
    try:
        with urlopen(f"{APP_URL.rstrip('/')}{path}", timeout=10) as response:
            return json.load(response)
    except URLError as error:
        raise RuntimeError(
            f"The FastAPI app is not reachable at {APP_URL}. Start Uvicorn before recording."
        ) from error


def verify_demo_state() -> None:
    summary = fetch_json("/api/analytics/summary")
    exposure = float(summary["total_at_risk_revenue"])
    if not math.isclose(exposure, TARGET_EXPOSURE, abs_tol=0.01):
        raise RuntimeError(
            f"Expected opening exposure ${TARGET_EXPOSURE:,.2f}, found ${exposure:,.2f}."
        )
    LOGGER.info("PREFLIGHT    opening exposure verified at $%s", f"{exposure:,.2f}")


def inject_recording_ui(page: Page) -> None:
    """Enlarge the product UI and add a cursor that is visible in headless video."""
    # Keep this as the first DOM mutation after navigation; Motion.so needs the
    # enlarged layout from the first recorded product frame onward.
    page.evaluate(f"document.body.style.zoom = '{UI_ZOOM}'")
    page.evaluate(
        """
        () => {
          document.body.style.textRendering = 'geometricPrecision';
          document.documentElement.style.cursor = 'none';
          const existing = document.querySelector('#demo-recording-cursor');
          if (existing) existing.remove();
          const cursor = document.createElement('div');
          cursor.id = 'demo-recording-cursor';
          cursor.setAttribute('aria-hidden', 'true');
          Object.assign(cursor.style, {
            position: 'fixed', left: '0', top: '0', width: '22px', height: '22px',
            borderRadius: '999px', pointerEvents: 'none', zIndex: '2147483647',
            border: '2px solid rgba(255,255,255,.96)',
            background: 'rgba(0,242,254,.34)',
            boxShadow: '0 0 0 5px rgba(0,242,254,.14), 0 0 22px rgba(0,242,254,.8)',
            transform: 'translate3d(-40px,-40px,0)',
            transition: 'width .16s ease, height .16s ease, background .16s ease'
          });
          document.documentElement.appendChild(cursor);
          document.addEventListener('mousemove', (event) => {
            cursor.style.transform = `translate3d(${event.clientX - 11}px, ${event.clientY - 11}px, 0)`;
          }, { passive: true });
          document.addEventListener('mousedown', () => {
            cursor.style.background = 'rgba(16,185,129,.78)';
          });
          document.addEventListener('mouseup', () => {
            cursor.style.background = 'rgba(0,242,254,.34)';
          });
        }
        """
    )
    page.wait_for_timeout(500)


def claim_row(page: Page, claim_id: str = TARGET_CLAIM_ID) -> Locator:
    return require_visible(
        page.locator("#claims-table-body tr").filter(has_text=claim_id),
        f"claim row {claim_id}",
    )


def verify_analytics_fit(page: Page) -> None:
    """Fail the recording if an analytics card or chart escapes the 2K frame."""
    result = page.evaluate(
        """
        () => {
          const viewportRight = window.innerWidth;
          const selectors = [
            '#workspace-analytics .analytics-chart-card',
            '#workspace-analytics .chart-stage',
            '#workspace-analytics .chart-stage canvas',
            '#workspace-analytics #payer-breakdown'
          ];
          const failures = [];
          for (const selector of selectors) {
            document.querySelectorAll(selector).forEach((element, index) => {
              const box = element.getBoundingClientRect();
              if (box.left < -1 || box.right > viewportRight + 1) {
                failures.push({ selector, index, left: box.left, right: box.right, viewportRight });
              }
            });
          }
          return { viewportRight, failures };
        }
        """
    )
    if result["failures"]:
        raise RuntimeError(f"Analytics clipping detected: {result['failures']}")
    LOGGER.info("LAYOUT       analytics cards and charts fit within %spx", result["viewportRight"])


def chart_targets(page: Page, chart_id: str, dataset_index: int) -> list[dict[str, object]]:
    """Return exact browser coordinates for visible Chart.js arcs or points."""
    targets = page.evaluate(
        """
        ({ chartId, datasetIndex }) => {
          const chart = window.RCMCharts?.getChart(chartId);
          if (!chart) return [];
          const canvasBox = chart.canvas.getBoundingClientRect();
          const scaleX = canvasBox.width / chart.width;
          const scaleY = canvasBox.height / chart.height;
          return chart.getDatasetMeta(datasetIndex).data.map((element, index) => {
            let x = element.x;
            let y = element.y;
            if (Number.isFinite(element.startAngle) && Number.isFinite(element.endAngle)) {
              const angle = (element.startAngle + element.endAngle) / 2;
              const radius = (element.innerRadius + element.outerRadius) / 2;
              x += Math.cos(angle) * radius;
              y += Math.sin(angle) * radius;
            }
            return {
              label: chart.data.labels[index],
              x: canvasBox.left + x * scaleX,
              y: canvasBox.top + y * scaleY,
            };
          });
        }
        """,
        {"chartId": chart_id, "datasetIndex": dataset_index},
    )
    return list(targets)


def tour_donut_chart(page: Page, cursor: SmoothCursor) -> None:
    """Hover every database-backed payer segment to surface its live tooltip."""
    desired_order = ["Cigna", "UHC", "Aetna", "BCBS"]
    targets = {str(item["label"]): item for item in chart_targets(page, "payer-denial-chart", 0)}
    missing = [payer for payer in desired_order if payer not in targets]
    if missing:
        raise RuntimeError(f"Missing payer segments in donut chart: {missing}")
    per_segment_ms = HOLD_CHART_MS // len(desired_order)
    for payer in desired_order:
        target = targets[payer]
        cursor.move_to_point(float(target["x"]), float(target["y"]), label=f"{payer} donut segment")
        hold(page, per_segment_ms, f"{payer} payer tooltip")


def tour_recovery_chart(page: Page, cursor: SmoothCursor) -> None:
    """Trace representative recovered-revenue line points with real tooltips."""
    targets = chart_targets(page, "recovery-trend-chart", 1)
    if not targets:
        raise RuntimeError("Revenue Recovery Trends chart has no hoverable points.")
    indexes = sorted({0, len(targets) // 3, (2 * len(targets)) // 3, len(targets) - 1})
    per_point_ms = HOLD_CHART_MS // len(indexes)
    for index in indexes:
        target = targets[index]
        cursor.move_to_point(
            float(target["x"]),
            float(target["y"]),
            label=f"recovery trend {target['label']}",
        )
        hold(page, per_point_ms, f"revenue tooltip {target['label']}")


def tour_dom_collection(
    page: Page,
    cursor: SmoothCursor,
    selector: str,
    total_hold_ms: int,
    *,
    label: str,
    maximum: int = 4,
) -> None:
    """Hover representative rendered cells/cards for a fixed total hold."""
    collection = page.locator(selector)
    count = min(collection.count(), maximum)
    if count == 0:
        raise RuntimeError(f"No visible targets found for {label}.")
    per_target_ms = total_hold_ms // count
    for index in range(count):
        target = require_visible(collection.nth(index), f"{label} {index + 1}")
        cursor.hover_and_hold(target, per_target_ms, label=f"{label} {index + 1}")


def record_feature_tour(page: Page) -> None:
    page.goto(APP_URL, wait_until="networkidle")
    inject_recording_ui(page)
    cursor = SmoothCursor(page)
    page.get_by_text("$2,842.75", exact=True).wait_for(state="visible", timeout=20_000)

    # Claims Queue: establish the pending work list before any state change.
    pending_filter = require_visible(
        page.locator(".filter-button[data-status='PENDING']"),
        "Pending filter",
    )
    cursor.click(pending_filter, label="Pending claims filter")
    require_visible(claim_row(page), "Lucas Anderson pending claim")
    page.locator("#detail-panel").wait_for(state="hidden", timeout=5_000)
    hold(page, HOLD_PENDING_MS, "Pending claims queue")

    # Analyze after the complete four-second pending establishing shot.
    cursor.click(button(page, "Analyze pending"), label="Analyze pending")
    hold(page, HOLD_ANALYSIS_MS, "AI batch analysis processing")
    analyzed_filter = require_visible(
        page.locator(".filter-button[data-status='ANALYZED']"),
        "Analyzed filter",
    )
    cursor.click(analyzed_filter, label="Analyzed claims filter")
    claim_row(page).get_by_text("ANALYZED", exact=True).wait_for(state="visible", timeout=30_000)

    # Customer detail & AI analysis.
    cursor.click(claim_row(page), label="Lucas Anderson claim MSC-10006")
    require_visible(page.get_by_text("Lucas Anderson", exact=True), "Lucas resolution board")
    cursor.hover_and_hold(
        require_visible(page.locator("#ai-result.ai-resolution"), "AI Analysis / Recommended Fix"),
        HOLD_RECOMMENDATION_MS,
        label="AI Analysis / Recommended Fix",
    )

    # Submit action. Current builds label this control Approve & Resubmit;
    # future demo builds may expose the shorter Submit Claim label.
    cursor.click(button(page, "Submit Claim", "Approve & Resubmit"), label="Submit Claim")
    require_visible(page.get_by_text("Resubmitted & Settled", exact=False), "resubmitted state")
    hold(page, HOLD_SUBMITTED_MS, "submitted / resolved state")

    # Sidebar navigation. Integration Hub remains intentionally excluded.
    cursor.click(button(page, "Executive Analytics"), label="Executive Analytics")
    require_visible(page.locator("#workspace-analytics"), "Executive Analytics workspace")
    hold(page, WORKSPACE_RENDER_MS, "analytics charts render and fit")
    verify_analytics_fit(page)

    cursor.hover_and_hold(
        require_visible(
            page.locator(".kpi-card").filter(has_text="First-pass resolution"),
            "First Pass Resolution KPI",
        ),
        HOLD_KPI_MS,
        label="First Pass Resolution KPI",
    )
    cursor.hover_and_hold(
        require_visible(
            page.locator(".kpi-card").filter(has_text="Automation coverage"),
            "Automation Coverage KPI",
        ),
        HOLD_KPI_MS,
        label="Automation Coverage KPI",
    )
    tour_donut_chart(page, cursor)
    tour_recovery_chart(page, cursor)
    tour_dom_collection(
        page,
        cursor,
        "#denial-heatmap .heatmap-cell",
        HOLD_CHART_MS,
        label="denial heatmap cell",
    )
    tour_dom_collection(
        page,
        cursor,
        "#payer-breakdown > div",
        HOLD_CHART_MS,
        label="financial exposure bar",
    )

    cursor.click(button(page, "Policy R&D Engine", "Policy RAG Engine"), label="Policy R&D Engine")
    require_visible(page.locator("#workspace-rag"), "Policy R&D workspace")
    hold(page, WORKSPACE_RENDER_MS, "policy index render")
    payer_select = require_visible(page.locator("#rag-payer"), "policy payer dropdown")
    cursor.click(payer_select, label="Policy payer dropdown")
    hold(page, CONTROL_SETTLE_MS, "payer dropdown open")
    payer_select.select_option("Cigna")
    hold(page, CONTROL_SETTLE_MS, "Cigna policy filter apply")
    policy_query = require_visible(page.locator("#rag-query"), "policy vector search")
    cursor.click(policy_query, label="Policy vector search")
    policy_query.fill("")
    policy_query.type("Modifier 25 reimbursement rule", delay=110)
    hold(page, CONTROL_SETTLE_MS, "policy query visible")
    cursor.click(button(page, "Search vectors"), label="Search vectors")
    require_visible(page.locator("#rag-results .rag-result"), "retrieved policy citation")
    tour_dom_collection(
        page,
        cursor,
        "#rag-results .rag-result",
        HOLD_POLICY_RESULTS_MS,
        label="retrieved policy citation",
        maximum=2,
    )

    cursor.click(button(page, "Audit Logs"), label="Audit Logs")
    require_visible(page.locator("#workspace-audit"), "Audit Logs workspace")
    hold(page, WORKSPACE_RENDER_MS, "audit log list render")
    audit_search = require_visible(page.locator("#audit-query"), "audit search and filter")
    cursor.click(audit_search, label="Audit Logs search")
    audit_search.fill("")
    audit_search.type(TARGET_CLAIM_ID, delay=150)
    hold(page, CONTROL_SETTLE_MS, "live audit search apply")
    action_filter = require_visible(page.locator("#audit-action"), "audit action filter")
    cursor.click(action_filter, label="Audit action filter")
    hold(page, CONTROL_SETTLE_MS, "audit action dropdown open")
    action_filter.select_option("CLAIM_RESUBMITTED")
    hold(page, CONTROL_SETTLE_MS, "resubmission action selected")
    cursor.click(button(page, "Apply filter"), label="Apply audit filter")
    hold(page, CONTROL_SETTLE_MS, "audit action filter apply")
    require_visible(
        page.locator("#global-audit-body tr")
        .filter(has_text=TARGET_CLAIM_ID)
        .filter(has_text="CLAIM_RESUBMITTED"),
        "filtered Lucas resubmission audit trail",
    )
    hold(page, HOLD_AUDIT_FILTERED_MS, "filtered Lucas audit logs")


def main() -> int:
    os.chdir(PROJECT_ROOT)
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    prepare_demo_state()
    verify_demo_state()

    browser: Browser | None = None
    context: BrowserContext | None = None
    video = None
    output_path = RECORDINGS_DIR / (
        f"mainstreet_rcm_2k_master_{datetime.now().strftime('%Y%m%d_%H%M%S')}.webm"
    )

    exit_code = 0
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport=VIDEO_SIZE,
                screen=VIDEO_SIZE,
                device_scale_factor=DEVICE_SCALE_FACTOR,
                record_video_dir=str(RECORDINGS_DIR),
                record_video_size=VIDEO_SIZE,
            )
            page = context.new_page()
            video = page.video
            record_feature_tour(page)
        except Exception:
            LOGGER.exception("2K master recording failed")
            exit_code = 1
        finally:
            # Closing the context is mandatory: it flushes Playwright's video encoder.
            if context is not None:
                context.close()
            if video is not None and exit_code == 0:
                # Save while Playwright's event loop is still active.
                video.save_as(str(output_path))
            if browser is not None:
                browser.close()

    if exit_code:
        return exit_code
    if video is None:
        LOGGER.error("Playwright did not create a video artifact.")
        return 1

    LOGGER.info("MASTER SAVED %s", output_path)
    LOGGER.info("RESOLUTION   2560x1440 full-frame")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
