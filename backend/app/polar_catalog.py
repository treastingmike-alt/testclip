"""Polar products sold by KlipCut.

Paste the long-lived Checkout Link URL and Product ID from the Polar dashboard
into the matching row below. Checkout links and product IDs identify public
products, so this catalog can be committed. Secrets never belong in this file.

Every tier is a separate Polar product. That keeps the amount charged in Polar
and the credits granted here tied to one immutable product ID instead of values
submitted by the browser.
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class PolarProduct:
    sku: str
    checkout_url: str
    product_id: str
    kind: str
    credits: int
    plan_id: str | None = None
    interval: str | None = None

    @property
    def grant_credits(self) -> int:
        """Credits delivered by one successful charge.

        Monthly plans renew monthly. Yearly plans charge once per year, so they
        receive all twelve monthly allowances with that paid annual order.
        """
        return self.credits * 12 if self.interval == "yearly" else self.credits


def _subscription(plan: str, credits: int, interval: str,
                  checkout_url: str = "", product_id: str = "") -> PolarProduct:
    return PolarProduct(
        sku=f"{plan}:{credits}:{interval}", checkout_url=checkout_url,
        product_id=product_id, kind="subscription", credits=credits,
        plan_id=plan, interval=interval,
    )


def _topup(credits: int, checkout_url: str = "",
           product_id: str = "") -> PolarProduct:
    return PolarProduct(
        sku=f"topup:{credits}", checkout_url=checkout_url,
        product_id=product_id, kind="topup", credits=credits,
    )


# ---------------------------------------------------------------------------
# PASTE POLAR CHECKOUT LINKS + PRODUCT IDS HERE
# ---------------------------------------------------------------------------
# Example configured product:
PRODUCTS = (
    _subscription("creator", 500, "monthly",
                  checkout_url="https://sandbox-api.polar.sh/v1/checkout-links/polar_cl_nLHCZTAf9FXRxjFjra0irLFYxoxSILCb1cQEW4QenX1/redirect",
                  product_id="ac083162-4d37-4809-9807-d69234a72cab"),
    _subscription("creator", 500, "yearly"),
    _subscription("creator", 1000, "monthly"),
    _subscription("creator", 1000, "yearly"),
    _subscription("creator", 1500, "monthly"),
    _subscription("creator", 1500, "yearly"),
    _subscription("creator", 2000, "monthly"),
    _subscription("creator", 2000, "yearly"),
    _subscription("pro", 1000, "monthly"),
    _subscription("pro", 1000, "yearly"),
    _topup(500),
    _topup(1500),
    _topup(5000),
)


def subscription_product(plan_id: str, credits: int,
                         interval: str) -> PolarProduct | None:
    sku = f"{plan_id}:{credits}:{interval}"
    return next((product for product in PRODUCTS if product.sku == sku), None)


def topup_product(credits: int) -> PolarProduct | None:
    sku = f"topup:{credits}"
    return next((product for product in PRODUCTS if product.sku == sku), None)


def product_by_id(product_id: str) -> PolarProduct | None:
    if not product_id:
        return None
    return next((product for product in PRODUCTS
                 if product.product_id and product.product_id == product_id), None)


def checkout_url(product: PolarProduct, user_id: str, email: str) -> str | None:
    """Attach reconciliation data without exposing a server credential."""
    raw = (product.checkout_url or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "polar.sh" or host.endswith(".polar.sh")):
        raise ValueError(f"{product.sku} does not contain a valid Polar Checkout Link")

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({
        "customer_email": email,
        "reference_id": user_id,
        "utm_source": "klipcut",
        "utm_medium": "app",
        "utm_campaign": product.sku,
    })
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                       urlencode(query), parsed.fragment))
