"""
Contact-detail backfill for Meta lead-ads leads.

A `leadgen` webhook carries identifiers only — never the prospect's name, email
or phone. Those live behind a Graph API call that needs a page access token,
which may be missing, expired or lacking `leads_retrieval` permission at the
moment the webhook arrives.

The rule this module exists to enforce: **a lead whose contact details could not
be retrieved is stored with those fields empty and an explicit statement of what
is missing and why.** Nothing is inferred, guessed, or filled with a plausible
placeholder that later reads as real. A sales team acting on an invented email
address is worse off than one that can see the record is incomplete.

Retrieval is retried later — on demand, or as a background job — and a retry
only ever *adds* information. It will replace the "unidentified" placeholder
name generated at ingest, but never a value a human has since corrected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.ai_ops import Integration
from app.models.leads import Lead, LeadActivity
from app.services.lead_ingest_service import (
    PROVIDER,
    LeadFetcher,
    _access_token,
    _EMAIL_KEYS,
    _field_map,
    _FIRST_NAME_KEYS,
    _LAST_NAME_KEYS,
    _NAME_KEYS,
    _PHONE_KEYS,
    _pick,
    fetch_lead_details,
)

logger = logging.getLogger("growthos.leads")

#: Written to `source_metadata.enrichment_status`.
COMPLETE = "complete"
PENDING = "pending"
FAILED = "failed"
UNAVAILABLE = "unavailable"

PLACEHOLDER_PREFIX = "Unidentified Meta lead"

NO_CONTACT_LIMITATION = (
    "Contact details unavailable from webhook payload; not yet retrieved from the Meta API."
)
NO_TOKEN_LIMITATION = (
    "Contact details cannot be retrieved: no valid Meta page access token is stored "
    "for this integration."
)


class BackfillUnavailable(Exception):
    """Retrieval cannot succeed until an operator fixes something."""


def has_contact_details(lead: Lead) -> bool:
    return bool((lead.email or "").strip() or (lead.phone or "").strip())


def is_placeholder_name(lead: Lead) -> bool:
    return (lead.name or "").startswith(PLACEHOLDER_PREFIX)


def needs_backfill(lead: Lead) -> bool:
    """A Meta lead we hold identifiers for but no way to contact."""
    if lead.source != "meta_lead_ads":
        return False
    return not has_contact_details(lead) or is_placeholder_name(lead)


def _limitations(lead: Lead, *, reason: str | None = None, retrieved: bool = False) -> list[str]:
    """
    State precisely which fields are missing, and only when that is meaningful.

    After a successful retrieval, whatever the API returned is the whole of what
    the prospect submitted: no phone number means the form did not ask for one,
    which is not a limitation of our data. Only an unusable lead — no way to
    contact them at all, or still no name — is worth flagging. When retrieval
    did not happen, every empty field is genuinely unknown and is listed.
    """
    missing = []
    if not (lead.email or "").strip():
        missing.append("email address")
    if not (lead.phone or "").strip():
        missing.append("phone number")
    if is_placeholder_name(lead):
        missing.append("name")

    if not missing:
        return []
    if retrieved and has_contact_details(lead) and not is_placeholder_name(lead):
        return []

    limitations = [f"Missing from this lead: {', '.join(missing)}."]
    limitations.append(reason or NO_CONTACT_LIMITATION)
    return limitations


def _mark(
    lead: Lead,
    *,
    status: str,
    reason: str | None = None,
    attempted: bool = True,
    retrieved: bool = False,
) -> None:
    metadata = dict(lead.source_metadata or {})
    metadata["enrichment_status"] = status
    metadata["contact_details_available"] = has_contact_details(lead)
    if attempted:
        metadata["enrichment_attempts"] = int(metadata.get("enrichment_attempts") or 0) + 1
        metadata["enrichment_last_attempt_at"] = datetime.now(timezone.utc).isoformat()
    if reason:
        metadata["enrichment_error"] = reason
    elif status == COMPLETE:
        metadata.pop("enrichment_error", None)

    limitations = _limitations(lead, reason=reason, retrieved=retrieved)
    if limitations:
        metadata["data_limitations"] = limitations
    else:
        metadata.pop("data_limitations", None)

    lead.source_metadata = metadata
    flag_modified(lead, "source_metadata")


async def _integration_for(db: AsyncSession, lead: Lead) -> Integration | None:
    rows = await db.scalars(
        select(Integration).where(
            Integration.provider == PROVIDER,
            Integration.organization_id == lead.organization_id,
        )
    )
    candidates = list(rows)
    page_id = str((lead.source_metadata or {}).get("page_id") or "")
    for row in candidates:
        config = row.config or {}
        known = {str(config.get("page_id") or ""), str(config.get("external_account_id") or "")}
        known.update(str(extra) for extra in (config.get("page_ids") or []))
        if page_id and page_id in {k for k in known if k}:
            return row
    # Fall back to the client's integration when the page id was not recorded.
    for row in candidates:
        if row.client_id == lead.client_id:
            return row
    return None


def _apply(lead: Lead, fields: dict[str, str]) -> list[str]:
    """Copy retrieved values onto the lead. Returns the field names that changed."""
    changed: list[str] = []

    email = _pick(fields, _EMAIL_KEYS)
    if email and not (lead.email or "").strip():
        lead.email = email
        changed.append("email")

    phone = _pick(fields, _PHONE_KEYS)
    if phone and not (lead.phone or "").strip():
        lead.phone = phone
        changed.append("phone")

    name = _pick(fields, _NAME_KEYS)
    if not name:
        first, last = _pick(fields, _FIRST_NAME_KEYS), _pick(fields, _LAST_NAME_KEYS)
        name = " ".join(part for part in (first, last) if part) or None
    # Only the ingest-time placeholder is replaced. A name a human has corrected
    # is not overwritten by a later provider fetch.
    if name and is_placeholder_name(lead):
        lead.name = name
        changed.append("name")

    return changed


async def backfill_lead_contact(
    db: AsyncSession,
    lead_id: UUID,
    *,
    fetcher: LeadFetcher | None = None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """
    Attempt one retrieval for one lead.

    Returns a description of what happened. Raises only when a retry could
    plausibly succeed — the job system then applies its backoff. A missing token
    is not raised: it is recorded on the lead, because retrying immediately
    cannot fix a configuration problem.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise BackfillUnavailable(f"Lead {lead_id} does not exist.")
    if organization_id is not None and lead.organization_id != organization_id:
        # Callers pass the caller's organization; a mismatch is a tenant breach.
        raise BackfillUnavailable(f"Lead {lead_id} does not exist.")

    if not needs_backfill(lead):
        return {"lead_id": str(lead_id), "status": "skipped", "reason": "already_complete"}

    leadgen_id = str((lead.source_metadata or {}).get("leadgen_id") or lead.external_id or "")
    if not leadgen_id:
        _mark(lead, status=UNAVAILABLE, reason="No Meta leadgen id recorded for this lead.")
        await db.flush()
        return {"lead_id": str(lead_id), "status": UNAVAILABLE, "reason": "no_leadgen_id"}

    integration = await _integration_for(db, lead)
    token = _access_token(integration) if integration else None
    if not token:
        _mark(lead, status=UNAVAILABLE, reason=NO_TOKEN_LIMITATION)
        await db.flush()
        logger.info(
            "Lead backfill unavailable",
            extra={"event": "lead.backfill", "outcome": "no_token", "lead": str(lead_id)},
        )
        return {"lead_id": str(lead_id), "status": UNAVAILABLE, "reason": "no_access_token"}

    call = fetcher or fetch_lead_details
    try:
        details = await call(leadgen_id, token)
    except Exception as exc:
        # Transient by assumption: record the attempt, then let the caller retry.
        _mark(lead, status=FAILED, reason=f"Meta API lookup failed: {type(exc).__name__}")
        await db.flush()
        logger.warning(
            "Lead backfill failed",
            extra={"event": "lead.backfill", "outcome": "error", "lead": str(lead_id)},
        )
        raise

    changed = _apply(lead, _field_map(details))
    complete = has_contact_details(lead) and not is_placeholder_name(lead)
    _mark(
        lead,
        status=COMPLETE if complete else PENDING,
        retrieved=True,
        reason=None
        if complete
        else "The Meta API response did not include contact details for this lead.",
    )

    if changed:
        lead.last_activity_at = datetime.now(timezone.utc)
        db.add(
            LeadActivity(
                organization_id=lead.organization_id,
                client_id=lead.client_id,
                lead_id=lead.id,
                activity_type="contact_backfilled",
                body=f"Contact details retrieved from the Meta API: {', '.join(changed)}.",
                meta={"fields": changed, "source": "meta_graph_api"},
            )
        )
    await db.flush()

    logger.info(
        "Lead backfill completed",
        extra={
            "event": "lead.backfill",
            "outcome": COMPLETE if complete else PENDING,
            "lead": str(lead_id),
            "fields": ",".join(changed) or None,
        },
    )
    return {
        "lead_id": str(lead_id),
        "status": COMPLETE if complete else PENDING,
        "updated_fields": changed,
    }


async def leads_awaiting_contact(
    db: AsyncSession, organization_id: UUID, *, client_id: UUID | None = None, limit: int = 100
) -> list[Lead]:
    query = select(Lead).where(
        Lead.organization_id == organization_id,
        Lead.source == "meta_lead_ads",
        or_(
            Lead.email.is_(None),
            Lead.email == "",
            Lead.name.startswith(PLACEHOLDER_PREFIX),
        ),
    )
    if client_id is not None:
        query = query.where(Lead.client_id == client_id)
    rows = await db.scalars(query.order_by(Lead.created_at.desc()).limit(limit))
    return [lead for lead in rows if needs_backfill(lead)]


async def enqueue_backfill(db: AsyncSession, lead: Lead) -> str | None:
    """
    Queue one retrieval attempt.

    The dedupe key is the lead id, so repeatedly asking for a backfill while one
    is already queued does not stack up duplicate Graph API calls against the
    same lead.
    """
    from app.jobs.queue import JobQueue
    from app.jobs.registry import LEAD_BACKFILL

    job = await JobQueue(db).enqueue(
        job_type=LEAD_BACKFILL,
        payload={"lead_id": str(lead.id), "organization_id": str(lead.organization_id)},
        organization_id=lead.organization_id,
        dedupe_key=f"{LEAD_BACKFILL}:{lead.id}",
    )
    return str(job.id) if job else None
