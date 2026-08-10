"""
Meta lead-ads webhook ingestion.

Honesty constraints that shape this module:

* A Meta `leadgen` webhook carries only identifiers (leadgen_id, form_id, ad_id,
  campaign_id, page_id). It does NOT carry the prospect's name or email — those
  must be fetched from the Graph API with the page access token. When that fetch
  is unavailable or fails, the lead is still persisted with the identifiers we
  actually received and is explicitly marked as awaiting enrichment. Contact
  details are never invented.
* The endpoint returns success only after the transaction commits, so Meta's
  retry behaviour remains meaningful.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_ops import Integration
from app.models.enums import LeadStatus
from app.models.leads import Lead, LeadActivity
from app.models.webhooks import WebhookEvent

PROVIDER = "meta"

# Meta field names we map onto first-class Lead columns.
_EMAIL_KEYS = ("email", "email_address", "work_email")
_PHONE_KEYS = ("phone_number", "phone", "mobile_number")
_NAME_KEYS = ("full_name", "name")
_FIRST_NAME_KEYS = ("first_name", "given_name")
_LAST_NAME_KEYS = ("last_name", "family_name", "surname")


class WebhookProcessingError(RuntimeError):
    """Processing failed in a way the provider should retry."""


class MalformedWebhookError(ValueError):
    """The payload is not a shape we can process. Retrying will not help."""


class AmbiguousPageRoutingError(RuntimeError):
    """
    Several integrations claim the same Meta page.

    Tenant ownership cannot be decided, so the event is quarantined instead of
    being filed under whichever row happened to be read first.
    """


@dataclass
class LeadgenEvent:
    """The identifiers Meta actually delivers for a lead-ads submission."""

    leadgen_id: str
    page_id: str | None
    form_id: str | None
    ad_id: str | None
    adgroup_id: str | None
    campaign_id: str | None
    created_time: int | None
    raw: dict[str, Any]


@dataclass
class IngestOutcome:
    processed: int = 0
    duplicates: int = 0
    unroutable: int = 0
    #: Events held back because more than one tenant claims the page.
    ambiguous: int = 0
    lead_ids: list[UUID] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.lead_ids is None:
            self.lead_ids = []


# --------------------------------------------------------------------------
# Payload parsing
# --------------------------------------------------------------------------


def parse_leadgen_events(payload: Any) -> list[LeadgenEvent]:
    """
    Extract leadgen events from a Meta page webhook payload.

    Raises MalformedWebhookError when the envelope itself is unusable. Entries
    for other subscription fields (comments, mentions) are skipped, not errors.
    """
    if not isinstance(payload, dict):
        raise MalformedWebhookError("Webhook body must be a JSON object.")
    if payload.get("object") != "page":
        raise MalformedWebhookError(f"Unsupported webhook object {payload.get('object')!r}; expected 'page'.")

    entries = payload.get("entry")
    if not isinstance(entries, list):
        raise MalformedWebhookError("Webhook 'entry' must be a list.")

    events: list[LeadgenEvent] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise MalformedWebhookError("Each 'entry' item must be an object.")
        entry_page_id = str(entry.get("id")) if entry.get("id") is not None else None
        changes = entry.get("changes") or []
        if not isinstance(changes, list):
            raise MalformedWebhookError("Webhook 'changes' must be a list.")
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "leadgen":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                raise MalformedWebhookError("leadgen 'value' must be an object.")
            leadgen_id = value.get("leadgen_id")
            if not leadgen_id:
                raise MalformedWebhookError("leadgen event is missing 'leadgen_id'.")
            events.append(
                LeadgenEvent(
                    leadgen_id=str(leadgen_id),
                    page_id=str(value.get("page_id") or entry_page_id or "") or None,
                    form_id=str(value["form_id"]) if value.get("form_id") else None,
                    ad_id=str(value["ad_id"]) if value.get("ad_id") else None,
                    adgroup_id=str(value["adgroup_id"]) if value.get("adgroup_id") else None,
                    campaign_id=str(value["campaign_id"]) if value.get("campaign_id") else None,
                    created_time=value.get("created_time") if isinstance(value.get("created_time"), int) else None,
                    raw=value,
                )
            )
    return events


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def page_ids_for(integration: Integration) -> set[str]:
    """Every Meta page identifier recorded on an integration at connect time."""
    config = integration.config or {}
    candidates = {
        str(config.get("page_id") or ""),
        str(config.get("external_account_id") or ""),
    }
    for extra in config.get("page_ids") or []:
        candidates.add(str(extra))
    return {value for value in candidates if value}


async def find_page_integrations(db: AsyncSession, *, page_id: str) -> list[Integration]:
    """
    Every Meta integration claiming this page, in a stable order.

    Returning the whole set rather than the first hit is the point: the caller
    has to see a conflict in order to refuse it.
    """
    rows = (
        (await db.execute(select(Integration).where(Integration.provider == PROVIDER)))
        .scalars()
        .all()
    )
    matches = [row for row in rows if page_id in page_ids_for(row)]
    return sorted(matches, key=lambda row: (str(row.organization_id), str(row.id)))


async def resolve_integration(db: AsyncSession, *, page_id: str | None) -> Integration | None:
    """
    Find the Meta integration that owns this page.

    Matching is by the page identifier recorded on the integration config at
    connect time. Two behaviours matter for tenancy:

    * No match — the event cannot be attributed and is not guessed at.
    * More than one match — ownership is genuinely undecidable, so this raises
      rather than picking one. Taking the first row would silently file one
      customer's prospects into another customer's CRM.
    """
    if not page_id:
        return None
    matches = await find_page_integrations(db, page_id=page_id)
    if not matches:
        return None
    if len(matches) > 1:
        owners = sorted({str(row.organization_id) for row in matches})
        raise AmbiguousPageRoutingError(
            f"page_id={page_id!r} is claimed by {len(matches)} Meta integrations "
            f"across {len(owners)} organization(s). Refusing to attribute the lead."
        )
    return matches[0]


# --------------------------------------------------------------------------
# Graph API enrichment (optional, never fabricated)
# --------------------------------------------------------------------------

LeadFetcher = Callable[[str, str], Awaitable[dict[str, Any]]]


async def fetch_lead_details(leadgen_id: str, access_token: str) -> dict[str, Any]:
    """Fetch the submitted field data for a lead from the Graph API."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"https://graph.facebook.com/v19.0/{leadgen_id}",
            params={"access_token": access_token, "fields": "field_data,created_time,ad_id,campaign_id,form_id"},
        )
        resp.raise_for_status()
        return resp.json()


def _field_map(details: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in details.get("field_data") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        values = item.get("values") or []
        if name and values:
            out[name] = str(values[0])
    return out


def _pick(fields: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = fields.get(key)
        if value:
            return value
    return None


def _access_token(integration: Integration) -> str | None:
    if not integration.secret_ref:
        return None
    try:
        from app.security.secrets import get_secret_store

        tokens = json.loads(get_secret_store().retrieve(integration.secret_ref))
    except Exception:
        return None
    if not isinstance(tokens, dict):
        return None
    return tokens.get("page_access_token") or tokens.get("access_token")


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


async def ingest_meta_webhook(
    db: AsyncSession,
    payload: Any,
    *,
    lead_fetcher: LeadFetcher | None = None,
) -> IngestOutcome:
    """
    Persist every leadgen event in the payload.

    Raises MalformedWebhookError for unusable payloads (respond 400 — do not retry)
    and WebhookProcessingError when persistence fails (respond 500 — please retry).
    """
    events = parse_leadgen_events(payload)
    outcome = IngestOutcome()
    fetcher = lead_fetcher or fetch_lead_details

    for event in events:
        if await _already_processed(db, event.leadgen_id):
            outcome.duplicates += 1
            continue
        try:
            lead_id = await _ingest_one(db, event, fetcher)
        except AmbiguousPageRoutingError:
            outcome.ambiguous += 1
            continue
        except IntegrityError:
            # Concurrent delivery of the same event won the unique constraint.
            await db.rollback()
            outcome.duplicates += 1
            continue
        except MalformedWebhookError:
            raise
        except Exception as exc:  # persistence or enrichment failure -> retryable
            await db.rollback()
            raise WebhookProcessingError(f"Failed to persist leadgen {event.leadgen_id}: {exc}") from exc

        if lead_id is None:
            outcome.unroutable += 1
        else:
            outcome.processed += 1
            outcome.lead_ids.append(lead_id)

    await db.commit()
    return outcome


async def _already_processed(db: AsyncSession, leadgen_id: str) -> bool:
    existing = await db.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == PROVIDER,
            WebhookEvent.event_id == leadgen_id,
            # `ambiguous` is included so a redelivery does not repeatedly retry a
            # routing conflict only an operator can resolve. The row stays on
            # record until the mapping is fixed and the event is replayed.
            WebhookEvent.status.in_(("processed", "unroutable", "ambiguous")),
        )
    )
    return existing is not None


async def _ingest_one(db: AsyncSession, event: LeadgenEvent, fetcher: LeadFetcher) -> UUID | None:
    record = WebhookEvent(
        provider=PROVIDER,
        event_id=event.leadgen_id,
        event_type="leadgen",
        status="received",
        payload=event.raw,
        attempts=1,
    )
    db.add(record)
    await db.flush()  # surfaces the dedup constraint before any lead is written

    try:
        integration = await resolve_integration(db, page_id=event.page_id)
    except AmbiguousPageRoutingError as exc:
        # Quarantine: the payload is kept so it can be replayed once an operator
        # has removed the duplicate page mapping, but no lead is written under a
        # guessed tenant.
        record.status = "ambiguous"
        record.error = f"{exc} Raw event retained for replay."
        record.processed_at = datetime.now(timezone.utc)
        await db.flush()
        raise

    if integration is None or integration.client_id is None:
        # Cannot attribute to a tenant. Keep the raw event so nothing is lost and
        # the lead can be replayed once the integration is connected.
        record.status = "unroutable"
        record.error = (
            f"No connected Meta integration with a client for page_id={event.page_id!r}. "
            "Raw event retained for replay."
        )
        record.processed_at = datetime.now(timezone.utc)
        await db.flush()
        return None

    record.organization_id = integration.organization_id
    record.client_id = integration.client_id

    lead = await _upsert_lead(db, event, integration, fetcher)

    record.lead_id = lead.id
    record.status = "processed"
    record.processed_at = datetime.now(timezone.utc)
    await db.flush()
    return lead.id


async def _upsert_lead(
    db: AsyncSession, event: LeadgenEvent, integration: Integration, fetcher: LeadFetcher
) -> Lead:
    existing = await db.scalar(
        select(Lead).where(
            Lead.organization_id == integration.organization_id,
            Lead.external_id == event.leadgen_id,
        )
    )
    if existing is not None:
        return existing

    details: dict[str, Any] = {}
    enrichment_error: str | None = None
    token = _access_token(integration)
    if token:
        try:
            details = await fetcher(event.leadgen_id, token)
        except Exception as exc:
            # Enrichment is best-effort. Losing the lead entirely would be worse
            # than storing it with identifiers only.
            enrichment_error = f"Graph API lookup failed: {exc}"
    else:
        enrichment_error = "No Meta page access token stored for this integration."

    fields = _field_map(details)
    name = _pick(fields, _NAME_KEYS)
    if not name:
        first, last = _pick(fields, _FIRST_NAME_KEYS), _pick(fields, _LAST_NAME_KEYS)
        name = " ".join(p for p in (first, last) if p) or None

    source_metadata = {
        "platform": "meta",
        "page_id": event.page_id,
        "form_id": event.form_id,
        "ad_id": event.ad_id,
        "adgroup_id": event.adgroup_id,
        "campaign_id": event.campaign_id,
        "leadgen_id": event.leadgen_id,
        "submitted_at": event.created_time,
        "contact_details_available": bool(name or _pick(fields, _EMAIL_KEYS)),
    }
    lead = Lead(
        organization_id=integration.organization_id,
        client_id=integration.client_id,
        # Placeholder is explicit about being unresolved rather than inventing a person.
        name=name or f"Unidentified Meta lead {event.leadgen_id}",
        email=_pick(fields, _EMAIL_KEYS),
        phone=_pick(fields, _PHONE_KEYS),
        source="meta_lead_ads",
        campaign=event.campaign_id,
        ad=event.ad_id or event.adgroup_id,
        external_id=event.leadgen_id,
        source_metadata=source_metadata,
        status=LeadStatus.new,
        last_activity_at=datetime.now(timezone.utc),
    )
    db.add(lead)
    await db.flush()

    # One vocabulary for "what is missing and why", shared with the retry path,
    # so a lead's state reads the same however it got there.
    from app.services.lead_backfill_service import (
        COMPLETE,
        PENDING,
        UNAVAILABLE,
        _mark,
        needs_backfill,
    )

    if needs_backfill(lead):
        _mark(
            lead,
            status=UNAVAILABLE if enrichment_error and not token else PENDING,
            reason=enrichment_error,
            attempted=bool(token),
        )
    else:
        _mark(lead, status=COMPLETE, attempted=bool(token))
    source_metadata = dict(lead.source_metadata or {})
    await db.flush()

    db.add(
        LeadActivity(
            organization_id=lead.organization_id,
            client_id=lead.client_id,
            lead_id=lead.id,
            activity_type="webhook_received",
            body="Lead received from Meta Lead Ads webhook.",
            meta=source_metadata,
        )
    )
    await db.flush()
    return lead
