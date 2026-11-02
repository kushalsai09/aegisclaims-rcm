from datetime import date
from decimal import Decimal

from app.database import SessionLocal, initialize_database
from app.models.claim import Claim


SEED_CLAIMS = [
    {
        "id": "MSC-10001",
        "patient_name": "Ava Williams",
        "insurer": "Blue Cross Blue Shield",
        "cpt_code": "99214",
        "denial_code": "CO-104",
        "denial_reason": "Service unbundled from another procedure on the same date.",
        "auth_number_in_chart": None,
        "modifier_attached": None,
        "diagnosis_code": "J06.9",
        "eob_notes": "Encounter note supports a significant, separately identifiable E/M service.",
        "amount": Decimal("285.00"),
        "date_of_service": date(2026, 8, 12),
    },
    {
        "id": "MSC-10002",
        "patient_name": "Liam Johnson",
        "insurer": "Aetna",
        "cpt_code": "87880",
        "denial_code": "CO-197",
        "denial_reason": "Precertification or authorization was absent.",
        "auth_number_in_chart": "PA-48102",
        "modifier_attached": None,
        "diagnosis_code": "J02.9",
        "eob_notes": "Authorization PA-48102 is present in the chart but was omitted from the claim.",
        "amount": Decimal("118.00"),
        "date_of_service": date(2026, 8, 14),
    },
    {
        "id": "MSC-10003",
        "patient_name": "Sophia Brown",
        "insurer": "UnitedHealthcare",
        "cpt_code": "71046",
        "denial_code": "CO-27",
        "denial_reason": "Expenses incurred after coverage terminated.",
        "auth_number_in_chart": None,
        "modifier_attached": None,
        "diagnosis_code": "R05.9",
        "eob_notes": "Eligibility response confirms coverage terminated before the date of service.",
        "amount": Decimal("172.50"),
        "date_of_service": date(2026, 8, 17),
    },
    {
        "id": "MSC-10004",
        "patient_name": "Noah Davis",
        "insurer": "Cigna",
        "cpt_code": "12002",
        "denial_code": "CO-104",
        "denial_reason": "Service considered part of a bundled procedure.",
        "auth_number_in_chart": None,
        "modifier_attached": None,
        "diagnosis_code": "S01.81XA",
        "eob_notes": "The denied repair line is integral to the comprehensive same-day service.",
        "amount": Decimal("346.25"),
        "date_of_service": date(2026, 8, 21),
    },
    {
        "id": "MSC-10005",
        "patient_name": "Mia Wilson",
        "insurer": "Blue Cross Blue Shield",
        "cpt_code": "87426",
        "denial_code": "CO-197",
        "denial_reason": "Required prior authorization was not obtained.",
        "auth_number_in_chart": "PA-77410",
        "modifier_attached": None,
        "diagnosis_code": "Z20.822",
        "eob_notes": "Authorization PA-77410 is documented in the chart and matches the service date.",
        "amount": Decimal("96.00"),
        "date_of_service": date(2026, 8, 25),
    },
    {
        "id": "MSC-10006",
        "patient_name": "Lucas Anderson",
        "insurer": "Cigna",
        "cpt_code": "99214",
        "denial_code": "CO-104",
        "denial_reason": "E/M service bundled with same-day procedure.",
        "auth_number_in_chart": None,
        "modifier_attached": None,
        "diagnosis_code": "M25.561",
        "eob_notes": "Encounter note supports a significant, separately identifiable E/M service.",
        "amount": Decimal("264.00"),
        "date_of_service": date(2026, 8, 27),
    },
    {
        "id": "MSC-10007",
        "patient_name": "Emma Thompson",
        "insurer": "UnitedHealthcare",
        "cpt_code": "72100",
        "denial_code": "CO-197",
        "denial_reason": "Authorization number missing from submitted claim.",
        "auth_number_in_chart": "UHC-88320",
        "modifier_attached": None,
        "diagnosis_code": "M54.50",
        "eob_notes": "Authorization UHC-88320 is documented and matches CPT 72100.",
        "amount": Decimal("238.50"),
        "date_of_service": date(2026, 8, 28),
    },
    {
        "id": "MSC-10008",
        "patient_name": "Mason Lee",
        "insurer": "UnitedHealthcare",
        "cpt_code": "99213",
        "denial_code": "CO-27",
        "denial_reason": "Coverage terminated before the service date.",
        "auth_number_in_chart": None,
        "modifier_attached": None,
        "diagnosis_code": "J01.90",
        "eob_notes": "Eligibility response shows coverage terminated 2026-08-01 before the service date.",
        "amount": Decimal("189.00"),
        "date_of_service": date(2026, 8, 29),
    },
]


def seed_database() -> int:
    """Idempotently insert or refresh the multi-payer development claims."""

    initialize_database()
    with SessionLocal() as db:
        for claim_data in SEED_CLAIMS:
            claim = db.get(Claim, claim_data["id"])
            if claim is None:
                db.add(Claim(**claim_data, status="PENDING"))
                continue
            for field, value in claim_data.items():
                setattr(claim, field, value)
        db.commit()
    return len(SEED_CLAIMS)


if __name__ == "__main__":
    count = seed_database()
    print(f"Seeded {count} claims into the database.")
