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


# Sandbox and production are separate Polar instances with separate tokens and
# separate product IDs. Explicit rather than guessed: a wrong guess either
# charges real cards from a test build or silently fails to find the product.
# Defaulting to production means a forgotten variable fails loudly at auth
# rather than quietly taking money in the wrong place.
POLAR_SERVER = os.environ.get("POLAR_SERVER", "production").strip() or "production"

# Where Polar returns the buyer once payment succeeds. Without it Polar ends on
# its own confirmation page and the customer never comes back to the app --
# which is exactly what happened: the payment worked and the user was stranded.
APP_ORIGIN = os.environ.get("CLIPPER_APP_ORIGIN", "").strip().rstrip("/")


def _client():
    token = os.environ.get("POLAR_ACCESS_TOKEN", "").strip()
    if not token:
        return None
    try:
        from polar_sdk import Polar
    except ImportError as exc:
        raise PolarConfigurationError("polar-sdk is not installed") from exc
    return Polar(access_token=token, server=POLAR_SERVER)


def create_checkout_session(product, user_id: str, email: str) -> str | None:
    """A checkout URL bound to this user, or None to fall back to a static link.

    Preferred over the pre-made Checkout Link because the three things that
    actually make fulfillment work cannot be set on a static link:

      success_url          -- brings the buyer back to the app afterwards
      metadata.reference_id-- what `_reference_id` matches the order to a user
      external_customer_id -- an independent second binding, so fulfillment
                              still resolves if metadata is ever dropped

    A checkout link can carry `reference_id` as a query parameter, but it only
    lands in the CHECKOUT SESSION's metadata; relying on it surviving into the
    order is a guess. Setting metadata directly removes the guess.
    """
    client = _client()
    if not client or not product or not product.product_id:
        return None

    payload = {
        "products": [product.product_id],
        "customer_email": email,
        "external_customer_id": user_id,
        # Read back by `_reference_id`. Keep the key in step with it.
        "metadata": {"reference_id": user_id, "sku": product.sku},
    }
    if APP_ORIGIN:
        # `payment=success` is the marker the app already watches for: it shows
        # the confirmation toast, re-polls the account a few times so a webhook
        # landing a second later still updates the balance, then strips both
        # params from the URL. Polar substitutes {CHECKOUT_ID}.
        payload["success_url"] = (
            f"{APP_ORIGIN}/?payment=success&checkout_id={{CHECKOUT_ID}}"
        )

    try:
        session = client.checkouts.create(request=payload)
    except Exception as exc:
        # Never a hard failure: the static link still works for buying, it just
        # needs the dashboard configured. Log loudly so this is diagnosable.
        print(f"[clipper] Polar checkout session failed ({product.sku}): {exc}")
        return None
    return getattr(session, "url", None)


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
    """Find the KlipCut user this Polar event belongs to.

    Several sources because Polar exposes the binding differently depending on
    how checkout was started, and a payment that cannot be matched to a user is
    the worst failure this system has: the money is taken and the credits never
    arrive.
    """
    metadata = data.get("metadata") or {}
    customer = data.get("customer") or {}
    checkout = data.get("checkout") or {}
    checkout_metadata = checkout.get("metadata") or {}
    return (metadata.get("reference_id")
            or metadata.get("klipcut_user_id")
            or customer.get("external_id")
            or data.get("external_customer_id")
            or customer.get("external_customer_id")
            # A checkout-link purchase puts reference_id on the SESSION, and
            # some payloads embed that session rather than copying its metadata.
            or checkout_metadata.get("reference_id"))


def _describe_unmatched(data: dict) -> str:
    """What a payload actually carried, for diagnosing a failed match.

    Deliberately keys and short scalars only -- this goes to the server log, and
    a payment payload is not something to dump wholesale.
    """
    customer = data.get("customer") or {}
    return (
        f"top-level keys={sorted(data.keys())[:12]} "
        f"metadata={sorted((data.get('metadata') or {}).keys())} "
        f"customer keys={sorted(customer.keys())[:10]} "
        f"external_customer_id={data.get('external_customer_id')!r} "
        f"customer.external_id={customer.get('external_id')!r} "
        f"customer.email={customer.get('email')!r}"
    )


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
            # Last resort: the email Polar charged. Weaker than a server-set
            # reference_id -- a buyer can edit the prefilled address at checkout
            # and credit someone else's account -- so it is a FALLBACK, never
            # the primary path, and it is logged every time it fires.
            #
            # The alternative is refusing the event, which means a real payment
            # is taken and the credits never arrive. Between crediting the
            # wrong account (requires deliberately paying to gift a stranger)
            # and crediting nobody (happens by accident, to a paying customer),
            # this is the better failure.
            email = ((data.get("customer") or {}).get("email")
                     or data.get("customer_email"))
            if email:
                user = (session.query(User)
                        .filter(User.email == email.strip().lower())
                        .first())
                if user:
                    print(f"[clipper] Polar {event_type}: no reference_id; matched "
                          f"by email {email!r} -> user {user.id}. Checkout should "
                          f"be creating sessions with metadata (see "
                          f"create_checkout_session).")

        if not user:
            session.rollback()
            raise PolarConfigurationError(
                "The Polar event has no valid KlipCut reference_id. "
                + _describe_unmatched(data)
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
