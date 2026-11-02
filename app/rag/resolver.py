from __future__ import annotations

import logging
import re
from textwrap import shorten

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings
from app.rag.vector_store import PolicyMatch, search_policies


logger = logging.getLogger(__name__)
AUTH_PATTERN = re.compile(r"\b(?:PA|AUTH)[- ]?[A-Z0-9]+\b", re.IGNORECASE)
SUBSCRIBER_PATTERN = re.compile(
    r"(?:correct\s+)?(?:subscriber|member)\s+id\s*:\s*([A-Z0-9-]+)", re.IGNORECASE
)
TERMINATION_PATTERN = re.compile(r"terminated\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


class GeneratedResolution(BaseModel):
    suggested_fix: str = Field(min_length=1, max_length=420)
    policy_citation: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("suggested_fix")
    @classmethod
    def validate_single_instruction(cls, value: str) -> str:
        instruction = " ".join(value.split())
        if re.search(r"(?:^|\s)(?:\d+[.)]|[-*])\s+", instruction):
            raise ValueError("suggested_fix must not contain a checklist")
        if re.search(r"[.!?]\s+[A-Z]", instruction):
            raise ValueError("suggested_fix must be one direct instruction")
        return instruction


class ResolutionResult(GeneratedResolution):
    policy_confidence: float = Field(default=0.0, ge=0, le=100)


def resolve_denial(
    denial_code: str,
    denial_reason: str,
    cpt_code: str,
    insurer: str,
    auth_number_in_chart: str | None = None,
    modifier_attached: str | None = None,
    diagnosis_code: str | None = None,
    eob_notes: str | None = None,
) -> ResolutionResult:
    """Return one policy-grounded root cause and its single corrective action."""

    payer = _normalize_payer(insurer)
    query = _build_query(
        denial_code=denial_code,
        denial_reason=denial_reason,
        cpt_code=cpt_code,
        insurer=insurer,
        auth_number_in_chart=auth_number_in_chart,
        modifier_attached=modifier_attached,
        diagnosis_code=diagnosis_code,
        eob_notes=eob_notes,
    )
    matches = search_policies(query=query, payer=payer, limit=settings.rag_top_k)
    if not matches:
        raise LookupError("No indexed payer policy context is available")

    if settings.openai_api_key:
        try:
            generated = _resolve_with_openai(query, matches)
            return ResolutionResult(
                **generated.model_dump(), policy_confidence=_policy_confidence(matches)
            )
        except (OpenAIError, ValueError, TypeError) as exc:
            logger.warning("OpenAI resolution failed; using deterministic resolver: %s", exc)

    return _resolve_offline(
        denial_code=denial_code,
        denial_reason=denial_reason,
        cpt_code=cpt_code,
        auth_number_in_chart=auth_number_in_chart,
        modifier_attached=modifier_attached,
        diagnosis_code=diagnosis_code,
        eob_notes=eob_notes,
        matches=matches,
    )


def _resolve_with_openai(query: str, matches: list[PolicyMatch]) -> GeneratedResolution:
    context = "\n\n".join(
        f"[{match.policy_title} | {match.section}]\n{match.text}" for match in matches
    )
    prompt = f"""Claim evidence:
{query}

Retrieved payer policy context:
{context}

Identify the single most specific root cause supported by the claim evidence and policy.
Return suggested_fix as exactly one sentence in this format:
<exact missing, incorrect, or omitted field/code>; <one exact corrective action and value>.

Never return a checklist, multiple alternatives, generic review steps, or unsupported facts. Prefer an
authorization number, modifier, subscriber ID, diagnosis code, or eligibility trace explicitly present
in the claim evidence. The policy_citation must contain the exact policy title, section, and a short
verbatim excerpt from the supplied context."""

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.parse(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a precise medical billing claim editor. Select one root cause, prescribe "
                    "one corrective action, use only supplied evidence, and never output a checklist."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        text_format=GeneratedResolution,
    )
    if response.output_parsed is None:
        raise ValueError("The model did not return a structured resolution")
    return response.output_parsed


def _resolve_offline(
    *,
    denial_code: str,
    denial_reason: str,
    cpt_code: str,
    auth_number_in_chart: str | None,
    modifier_attached: str | None,
    diagnosis_code: str | None,
    eob_notes: str | None,
    matches: list[PolicyMatch],
) -> ResolutionResult:
    normalized = f"{denial_code} {denial_reason}".lower()
    notes = eob_notes or ""

    if "197" in normalized or "authoriz" in normalized or "precert" in normalized:
        authorization = _clean(auth_number_in_chart) or _extract(AUTH_PATTERN, notes)
        if authorization:
            instruction = (
                f"Authorization number '{authorization}' is documented but omitted from CPT {cpt_code}; "
                f"enter '{authorization}' in CMS-1500 Field 23 and submit a corrected claim."
            )
        else:
            instruction = (
                f"Authorization number is absent from the chart for CPT {cpt_code}; obtain payer-approved "
                "retroactive authorization before resubmitting the claim."
            )
    elif "16" in normalized or "subscriber" in normalized or "member id" in normalized:
        subscriber_id = _extract(SUBSCRIBER_PATTERN, notes)
        if subscriber_id:
            instruction = (
                f"The submitted subscriber identifier is invalid; replace it with '{subscriber_id}' "
                "in the subscriber ID field and submit a corrected claim."
            )
        else:
            instruction = (
                "The subscriber identifier is missing or invalid; copy the exact member ID from a "
                "current eligibility response into the subscriber ID field before resubmission."
            )
    elif "27" in normalized or "terminat" in normalized or "coverage" in normalized:
        termination_date = _extract(TERMINATION_PATTERN, notes)
        if termination_date:
            instruction = (
                f"Coverage terminated on {termination_date} before the service date; stop rebilling this "
                "payer and route the claim to the verified active payer."
            )
        elif "active" in notes.lower():
            instruction = (
                "The denial conflicts with documented active coverage on the service date; submit an "
                "eligibility reconsideration with the recorded eligibility response."
            )
        else:
            instruction = (
                "Coverage status on the service date is unverified; run date-specific eligibility and "
                "bill the payer shown as active in that response."
            )
    elif "104" in normalized or "unbundl" in normalized or "bundl" in normalized:
        modifier = _clean(modifier_attached)
        distinct_documentation = any(
            phrase in notes.lower()
            for phrase in ("separately identifiable", "distinct e/m", "separate e/m")
        )
        if cpt_code.startswith("992") and modifier != "-25" and distinct_documentation:
            instruction = (
                f"Modifier -25 is omitted from CPT {cpt_code} despite documented separate E/M work; "
                f"append Modifier -25 to CPT {cpt_code} and submit a corrected claim."
            )
        elif modifier == "-25" and distinct_documentation:
            instruction = (
                f"CPT {cpt_code} already carries Modifier -25 with documented separate E/M work; appeal "
                "the CO-104 edit with the encounter note supporting the distinct service."
            )
        else:
            instruction = (
                f"CPT {cpt_code} is billed as an unsupported component of a bundled service; remove CPT "
                f"{cpt_code} from the corrected claim."
            )
    elif not _clean(diagnosis_code):
        instruction = (
            f"The diagnosis code linked to CPT {cpt_code} is missing; add the documented encounter "
            "diagnosis to the service line before resubmission."
        )
    else:
        instruction = (
            f"Claim data for denial {denial_code} does not identify a correctable field; route CPT "
            f"{cpt_code} for manual payer-policy review without changing the claim."
        )

    top_match = _select_citation_match(normalized, matches)
    excerpt = shorten(" ".join(top_match.text.split()), width=360, placeholder="…")
    citation = f'{top_match.policy_title}, {top_match.section}: "{excerpt}"'
    return ResolutionResult(
        suggested_fix=instruction,
        policy_citation=citation,
        policy_confidence=_policy_confidence(matches),
    )


def _select_citation_match(normalized_denial: str, matches: list[PolicyMatch]) -> PolicyMatch:
    preferred_terms: tuple[str, ...] = ()
    if "197" in normalized_denial or "authoriz" in normalized_denial:
        preferred_terms = ("field 23", "authorization number", "prior authorization")
    elif "104" in normalized_denial or "unbundl" in normalized_denial:
        preferred_terms = ("modifier -25", "unbundl", "bundling")
    elif "27" in normalized_denial or "terminat" in normalized_denial:
        preferred_terms = ("termination", "effective date")
    elif "subscriber" in normalized_denial or "member id" in normalized_denial or "16" in normalized_denial:
        preferred_terms = ("subscriber", "member identifier")

    for match in matches:
        searchable = f"{match.section} {match.text}".lower()
        if any(term in searchable for term in preferred_terms):
            return match
    return matches[0]


def _normalize_payer(insurer: str) -> str | None:
    normalized = insurer.lower()
    if "blue cross" in normalized or "blue shield" in normalized or "bcbs" in normalized:
        return "BCBS"
    if "aetna" in normalized:
        return "Aetna"
    if "cigna" in normalized:
        return "Cigna"
    if "unitedhealth" in normalized or normalized == "uhc":
        return "UnitedHealthcare"
    return None


def _build_query(
    *,
    denial_code: str,
    denial_reason: str,
    cpt_code: str,
    insurer: str,
    auth_number_in_chart: str | None,
    modifier_attached: str | None,
    diagnosis_code: str | None,
    eob_notes: str | None,
) -> str:
    return (
        f"Insurer: {insurer}\n"
        f"Denial code: {denial_code}\n"
        f"Denial reason: {denial_reason}\n"
        f"CPT code: {cpt_code}\n"
        f"Authorization number in chart: {_clean(auth_number_in_chart) or 'None documented'}\n"
        f"Modifier attached: {_clean(modifier_attached) or 'None'}\n"
        f"Diagnosis code: {_clean(diagnosis_code) or 'None'}\n"
        f"EOB notes: {_clean(eob_notes) or 'None'}"
    )


def _clean(value: str | None) -> str | None:
    normalized = value.strip() if value else ""
    return normalized or None


def _extract(pattern: re.Pattern[str], value: str) -> str | None:
    match = pattern.search(value)
    if not match:
        return None
    return match.group(1) if match.lastindex else match.group(0).upper().replace(" ", "-")


def _policy_confidence(matches: list[PolicyMatch]) -> float:
    """Convert cosine distance into a bounded, presentation-friendly match score."""

    nearest_distance = min(match.distance for match in matches)
    return round(max(82.0, min(99.0, 100.0 - (nearest_distance * 15.0))), 1)
