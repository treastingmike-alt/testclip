"""Signed, idempotent Polar webhook fulfillment."""

import hashlib
import json
import os

from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import (CreditLedger, PolarOrderGrant, PolarSubscription,
                        PolarWebhookEvent, User)
from app.polar_catalog import product_by_id
from app.jobs import extend_user_retention


class PolarConfigurationError(RuntimeError):
    pass


def validate_webhook(body: bytes, headers) -> dict:
    """Verify the Standard Webhooks signature with Polar's official SDK."""
    secret = os.environ.get("POLAR_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise PolarConfigurationError("POLAR_WEBHOOK_SECRET is not configured")
    try:
        from polar_sdk.webhooks import validate_event
    except ImportError as exc:
        raise PolarConfigurationError("polar-sdk is not installed") from exc

    event = validate_event(body=body, headers=dict(headers), secret=secret)
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    if isinstance(event, dict):
        return event
    return json.loads(event.json())


def delivery_id(headers, body: bytes) -> str:
    """Standard Webhooks IDs deliveries; hash is a deterministic fallback."""
    return (headers.get("webhook-id") or headers.get("Webhook-Id")
            or hashlib.sha256(body).hexdigest())


def _reference_id(data: dict) -> str | None:
    metadata = data.get("metadata") or {}
    customer = data.get("customer") or {}
    return (metadata.get("reference_id") or metadata.get("klipcut_user_id")
            or customer.get("external_id"))


def _set_subscription(session, user: User, data: dict, product, status: str) -> None:
    subscription_id = data.get("id") or data.get("subscription_id")
    if not subscription_id:
        return
    row = session.get(PolarSubscription, subscription_id)
    if not row:
        row = PolarSubscription(id=subscription_id, user_id=user.id)
        session.add(row)
    row.product_id = data.get("product_id") or product.product_id
    row.plan_id = product.plan_id
    row.status = status

    if status in {"active", "trialing"}:
        user.plan = product.plan_id
    elif status == "revoked":
        other = (session.query(PolarSubscription)
                 .filter(PolarSubscription.user_id == user.id,
                         PolarSubscription.id != subscription_id,
                         PolarSubscription.status.in_(("active", "trialing")))
                 .first())
        if not other:
            user.plan = "free"


def handle_event(event: dict, event_id: str) -> dict:
    """Apply one verified event exactly once, including Polar retries."""
    event_type = event.get("type", "")
    data = event.get("data") or {}

    with SessionLocal() as session:
        if session.get(PolarWebhookEvent, event_id):
            return {"status": "duplicate"}
        session.add(PolarWebhookEvent(id=event_id, event_type=event_type))
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return {"status": "duplicate"}

        if event_type not in {
            "order.paid", "subscription.active", "subscription.updated",
            "subscription.canceled", "subscription.uncanceled",
            "subscription.past_due", "subscription.revoked",
        }:
            session.commit()
            return {"status": "ignored"}

        product = product_by_id(data.get("product_id"))
        if not product:
            session.rollback()
            raise PolarConfigurationError(
                f"No KlipCut catalog entry matches Polar product {data.get('product_id')!r}"
            )
        user_id = _reference_id(data)
        user = session.get(User, user_id) if user_id else None
        if not user:
            session.rollback()
            raise PolarConfigurationError(
                "The Polar event has no valid KlipCut reference_id"
            )

        if event_type == "order.paid":
            order_id = data.get("id")
            if not order_id:
                session.rollback()
                raise PolarConfigurationError("Paid order has no ID")
            if session.get(PolarOrderGrant, order_id):
                session.rollback()
                return {"status": "duplicate"}

            # Flush the unique order marker before touching the balance. Even a
            # redelivery with a different webhook ID cannot grant twice.
            session.add(PolarOrderGrant(
                order_id=order_id, user_id=user.id,
                product_id=product.product_id, credits=product.grant_credits,
            ))
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                return {"status": "duplicate"}

            user.credits += product.grant_credits
            note = (f"Polar {product.plan_id.title()} renewal"
                    if product.kind == "subscription"
                    else "Polar credit top-up")
            session.add(CreditLedger(
                user_id=user.id, delta=product.grant_credits,
                balance_after=user.credits, note=note,
            ))
            if product.kind == "subscription":
                user.plan = product.plan_id
                extend_user_retention(session, user.id, hours=72)
                subscription_data = dict(data)
                subscription_data["id"] = data.get("subscription_id")
                _set_subscription(session, user, subscription_data, product, "active")

        elif product.kind == "subscription":
            status = data.get("status") or event_type.removeprefix("subscription.")
            if event_type == "subscription.revoked":
                status = "revoked"
            _set_subscription(session, user, data, product, status)
            if status in {"active", "trialing"}:
                extend_user_retention(session, user.id, hours=72)

        session.commit()
        return {"status": "applied", "event_type": event_type}
