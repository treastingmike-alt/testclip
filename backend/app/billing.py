"""Plans, credit pricing, and metering.

No payment provider is wired in yet -- deliberately. Everything a provider needs
to plug into exists here (plans, a credit balance, a ledger of debits, a checkout
endpoint that currently only records intent), so adding Polar/Stripe later means
implementing one webhook that calls `grant_credits`, not restructuring anything.

Credits are priced per MINUTE OF SOURCE VIDEO rather than per clip. Cost is
driven by transcription and the analysis passes, both of which scale with how
long the input is -- a 60-minute podcast costs the same to analyse whether the
user asks for 1 clip or 10. Charging per clip would let a 3-hour stream be
processed for the price of one short.
"""

from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import CreditLedger, User

# 1 credit == 1 minute of source video, matching how this market prices.
SECONDS_PER_CREDIT = 60.0
MIN_CHARGE = 1
FREE_SIGNUP_CREDITS = 100

PLANS = [
    {
        "id": "free",
        "name": "Free",
        "blurb": "Try it on a few videos",
        "monthly_usd": 0,
        "yearly_usd": 0,
        "credits": FREE_SIGNUP_CREDITS,
        "features": [
            f"{FREE_SIGNUP_CREDITS} credits on signup",
            "All templates and ratios",
            "Word-accurate captions",
            "Clip trimming",
        ],
    },
    {
        "id": "creator",
        "name": "Creator",
        "blurb": "For posting consistently",
        "credits": 1000,           # default tier -- the headline price
        "monthly_usd": 19,
        "yearly_usd": 15,          # per month, billed yearly
        "popular": True,
        # Same plan, more credits per month. Bigger tiers cost less per credit,
        # since the fixed cost of a plan is amortised over more usage.
        "tiers": [
            {"credits": 500, "monthly_usd": 12, "yearly_usd": 10},
            {"credits": 1000, "monthly_usd": 19, "yearly_usd": 15},
            {"credits": 1500, "monthly_usd": 27, "yearly_usd": 22},
            {"credits": 2000, "monthly_usd": 34, "yearly_usd": 27},
            {"credits": 3000, "monthly_usd": 48, "yearly_usd": 38},
        ],
        "features": [
            "≈ 100 clips from hour-long videos",
            "Gameplay split-screen",
            "Pause tightening",
            "Trim and extend any clip",
            "Priority rendering",
        ],
    },
    {
        "id": "pro",
        "name": "Pro",
        "blurb": "For teams and agencies",
        "credits": 3000,
        "monthly_usd": 49,
        "yearly_usd": 39,
        "tiers": [
            {"credits": 3000, "monthly_usd": 49, "yearly_usd": 39},
            {"credits": 5000, "monthly_usd": 75, "yearly_usd": 60},
            {"credits": 8000, "monthly_usd": 112, "yearly_usd": 90},
            {"credits": 12000, "monthly_usd": 156, "yearly_usd": 125},
        ],
        "features": [
            "Everything in Creator",
            "Bulk exports",
            "Custom gameplay library",
            "Team seats",
        ],
    },
]

# Extra credits outside a plan.
TOPUPS = [
    {"credits": 500, "usd": 12},
    {"credits": 1500, "usd": 30},
    {"credits": 5000, "usd": 90},
]


def credits_for(duration_seconds: float) -> int:
    """Credits a source video of this length costs. Always at least MIN_CHARGE."""
    if not duration_seconds or duration_seconds <= 0:
        return MIN_CHARGE
    import math
    return max(MIN_CHARGE, math.ceil(duration_seconds / SECONDS_PER_CREDIT))


def get_plan(plan_id: str) -> dict:
    for plan in PLANS:
        if plan["id"] == plan_id:
            return plan
    return None


def get_tier(plan_id: str, credits: int) -> dict:
    """The priced tier for a plan at a given monthly credit amount.

    Falls back to the plan's headline tier when credits are not specified, and
    returns None for a combination that is not offered -- so a client cannot
    invent its own price by posting an arbitrary credit count.
    """
    plan = get_plan(plan_id)
    if not plan:
        return None

    tiers = plan.get("tiers")
    if not tiers:
        return {"credits": plan["credits"],
                "monthly_usd": plan["monthly_usd"],
                "yearly_usd": plan["yearly_usd"]}

    if credits is None:
        credits = plan["credits"]
    return next((t for t in tiers if t["credits"] == credits), None)


def balance(user_id: str) -> int:
    with SessionLocal() as session:
        user = session.get(User, user_id)
        return user.credits if user else 0


def charge(user_id: str, amount: int, job_id: str = None, note: str = "") -> bool:
    """Debits credits. Returns False (and changes nothing) if the balance is short.

    Anonymous use is free while metering is unenforced, so a null user_id is a
    no-op rather than an error.
    """
    if not user_id:
        return True

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if not user or user.credits < amount:
            return False
        user.credits -= amount
        session.add(CreditLedger(user_id=user_id, delta=-amount,
                                 balance_after=user.credits,
                                 job_id=job_id, note=note or "clip job"))
        session.commit()
        return True


def grant_credits(user_id: str, amount: int, note: str = "purchase") -> int:
    """Adds credits. This is the single call a payment webhook needs to make."""
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if not user:
            return 0
        user.credits += amount
        session.add(CreditLedger(user_id=user_id, delta=amount,
                                 balance_after=user.credits, note=note))
        session.commit()
        return user.credits


def history(user_id: str, limit: int = 50) -> list:
    with SessionLocal() as session:
        rows = (session.query(CreditLedger)
                .filter(CreditLedger.user_id == user_id)
                .order_by(CreditLedger.created_at.desc())
                .limit(limit).all())
        return [{
            "delta": r.delta,
            "balance_after": r.balance_after,
            "note": r.note,
            "job_id": r.job_id,
            "at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
