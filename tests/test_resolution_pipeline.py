import unittest
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.audit import AuditLog  # noqa: F401 - registers the table metadata
from app.models.claim import Claim


class ResolutionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(
                Claim(
                    id="FLOW-1001",
                    patient_name="Reactive Test",
                    insurer="Cigna",
                    cpt_code="99214",
                    denial_code="CO-104",
                    denial_reason="Bundled service",
                    amount=Decimal("264.00"),
                    date_of_service=date(2026, 9, 1),
                    status="ANALYZED",
                    suggested_fix="Append Modifier -25 to CPT 99214 and submit a corrected claim.",
                    policy_citation="Cigna policy, Modifier -25 section.",
                )
            )
            session.commit()

        def override_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()

    def test_resubmit_updates_claim_audit_and_revenue(self) -> None:
        before = self.client.get("/api/analytics/summary").json()
        response = self.client.post("/api/claims/FLOW-1001/resolve")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "RESOLVED")

        logs = self.client.get("/api/claims/FLOW-1001/audit-logs").json()
        self.assertEqual(logs[0]["action"], "CLAIM_RESUBMITTED")
        self.assertEqual(logs[0]["performed_by"], "Billing Operations")

        after = self.client.get("/api/analytics/summary").json()
        self.assertEqual(after["total_recovered_revenue"], 264.0)
        self.assertEqual(
            before["total_at_risk_revenue"] - after["total_at_risk_revenue"],
            264.0,
        )


if __name__ == "__main__":
    unittest.main()
