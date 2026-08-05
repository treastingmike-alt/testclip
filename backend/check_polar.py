"""Check that the Polar configuration and the local catalog agree.

Run this after setting POLAR_ACCESS_TOKEN and before trying a purchase:

    POLAR_ACCESS_TOKEN=polar_oat_... POLAR_SERVER=sandbox python check_polar.py

It answers the three questions that each cause a silent, identical-looking
failure -- checkout completes on Polar's page and no credits appear:

  1. Is the token real, and pointed at the right environment?
  2. Does every product_id in polar_catalog.py exist in that environment?
  3. Is anything sellable in the UI missing a product_id entirely?

Sandbox and production are separate instances with separate tokens and separate
product IDs, so a token from one silently finds nothing in the other.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.polar_catalog import PRODUCTS  # noqa: E402


def main() -> int:
    token = os.environ.get("POLAR_ACCESS_TOKEN", "").strip()
    server = os.environ.get("POLAR_SERVER", "production").strip() or "production"

    if not token:
        print("POLAR_ACCESS_TOKEN is not set.")
        print("Create one at Polar > Settings > Developers > New Token.")
        return 1

    import re
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                token, re.I):
        print("POLAR_ACCESS_TOKEN looks like a UUID -- that is the Organization")
        print("ID shown at the top of settings, not an access token.")
        return 1

    from polar_sdk import Polar
    client = Polar(access_token=token, server=server)
    print(f"Server: {server}\n")

    try:
        remote = {}
        page = client.products.list(limit=100)
        while page is not None:
            for item in page.result.items:
                remote[item.id] = item.name
            page = page.next()
    except Exception as exc:
        print(f"Could not list products: {exc}")
        print("A 401 here means the token is wrong, expired, or from the other "
              "environment (sandbox vs production).")
        return 1

    print(f"{len(remote)} product(s) visible in this environment:")
    for pid, name in remote.items():
        print(f"   {pid}  {name}")
    print()

    problems = 0
    configured = [p for p in PRODUCTS if p.product_id]
    for product in configured:
        if product.product_id in remote:
            print(f"  OK       {product.sku:28} -> {remote[product.product_id]}")
        else:
            problems += 1
            print(f"  MISSING  {product.sku:28} -> {product.product_id}")
            print(f"           not in {server}. Wrong environment, or a stale ID.")

    unconfigured = [p for p in PRODUCTS if not p.product_id]
    if unconfigured:
        print(f"\n{len(unconfigured)} catalog entr(ies) have no product_id and "
              f"cannot be bought:")
        for product in unconfigured:
            print(f"   {product.sku}")
        print("Checkout returns 'provider_not_configured' for these, so the UI "
              "must not default to one.")

    if problems:
        print(f"\n{problems} configured product(s) do not exist. A purchase of "
              f"one of those would be charged and never fulfilled: the webhook "
              f"cannot map the order back to a plan.")
        return 1

    print("\nCatalog matches Polar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
