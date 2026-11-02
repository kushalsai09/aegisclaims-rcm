import unittest
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.claim import Claim


class AnalyticsEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(cls.engine)
        claims = [
            ("T-1", "BCBS", "CO-104", "Unbundled service", "285.00", "RESOLVED"),
            ("T-2", "BCBS", "CO-197", "Authorization absent", "96.00", "PENDING"),
            ("T-3", "Aetna", "CO-197", "Authorization absent", "118.00", "RESOLVED"),
            ("T-4", "Cigna", "CO-104", "Bundled service", "346.25", "PENDING"),
            ("T-5", "UnitedHealthcare", "CO-27", "Coverage terminated", "172.50", "ANALYZED"),
        ]
        with Session(cls.engine) as session:
            for claim_id, insurer, denial_code, reason, amount, status in claims:
                session.add(
                    Claim(
                        id=claim_id,
                        patient_name=f"Patient {claim_id}",
                        insurer=insurer,
                        cpt_code="99214",
                        denial_code=denial_code,
                        denial_reason=reason,
                        amount=Decimal(amount),
                        date_of_service=date(2026, 9, 1),
                        status=status,
                    )
                )
            session.commit()

        def override_db():
            with Session(cls.engine) as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.clear()
        cls.client.close()
        cls.engine.dispose()

    def test_summary_endpoint_returns_expected_financial_metrics(self) -> None:
        response = self.client.get("/api/analytics/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_claims"], 5)
        self.assertEqual(payload["pending_claims"], 2)
        self.assertEqual(payload["analyzed_claims"], 1)
        self.assertEqual(payload["resolved_claims"], 2)
        self.assertEqual(payload["total_at_risk_revenue"], 614.75)
        self.assertEqual(payload["total_recovered_revenue"], 403.0)
        self.assertEqual(payload["total_billed_revenue"], 1017.75)
        self.assertEqual(payload["ai_analyzed_claims"], 3)
        self.assertEqual(payload["ai_resolution_rate"], 66.67)
        self.assertEqual(payload["first_pass_resolution_rate"], 40.0)
        self.assertEqual(payload["automation_coverage_rate"], 60.0)
        self.assertEqual(payload["average_claim_value"], 203.55)
        self.assertEqual(len(payload["recovery_trends"]), 1)
        self.assertEqual(payload["recovery_trends"][0]["recovered_revenue"], 403.0)

        denial_codes = {item["denial_code"]: item for item in payload["top_denial_codes"]}
        self.assertEqual(denial_codes["CO-104"]["claim_count"], 2)
        self.assertEqual(denial_codes["CO-104"]["resolution_success_rate"], 50.0)
        self.assertEqual(denial_codes["CO-197"]["total_revenue"], 214.0)


if __name__ == "__main__":
    unittest.main()
