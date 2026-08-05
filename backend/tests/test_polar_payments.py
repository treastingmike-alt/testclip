from urllib.parse import parse_qs, urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CreditLedger, PolarOrderGrant, User
from app.polar_catalog import PolarProduct, checkout_url
from app import polar_payments


def test_checkout_link_is_bound_to_signed_in_user():
    product = PolarProduct(
        sku="creator:1000:monthly",
        checkout_url="https://buy.polar.sh/example?locale=en",
        product_id="product-1", kind="subscription", credits=1000,
        plan_id="creator", interval="monthly",
    )
    result = checkout_url(product, "user-123", "creator@example.com")
    query = parse_qs(urlsplit(result).query)
    assert query["reference_id"] == ["user-123"]
    assert query["customer_email"] == ["creator@example.com"]
    assert query["locale"] == ["en"]


def test_yearly_subscription_grants_twelve_months_of_credits():
    product = PolarProduct(
        sku="creator:1000:yearly", checkout_url="https://buy.polar.sh/example",
        product_id="product-yearly", kind="subscription", credits=1000,
        plan_id="creator", interval="yearly",
    )
    assert product.grant_credits == 12000


def test_paid_order_grants_once_even_with_a_new_delivery_id(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(polar_payments, "SessionLocal", sessions)

    product = PolarProduct(
        sku="creator:1000:monthly", checkout_url="https://buy.polar.sh/example",
        product_id="product-1", kind="subscription", credits=1000,
        plan_id="creator", interval="monthly",
    )
    monkeypatch.setattr(polar_payments, "product_by_id",
                        lambda product_id: product if product_id == "product-1" else None)

    with sessions() as session:
        session.add(User(id="user-123", email="creator@example.com",
                         password_hash="unused", credits=20, plan="free"))
        session.commit()

    event = {
        "type": "order.paid",
        "data": {
            "id": "order-1", "product_id": "product-1",
            "subscription_id": "subscription-1",
            "metadata": {"reference_id": "user-123"},
        },
    }
    assert polar_payments.handle_event(event, "delivery-1")["status"] == "applied"
    assert polar_payments.handle_event(event, "delivery-2")["status"] == "duplicate"

    with sessions() as session:
        user = session.get(User, "user-123")
        assert user.credits == 1020
        assert user.plan == "creator"
        assert session.query(PolarOrderGrant).count() == 1
        assert session.query(CreditLedger).filter(CreditLedger.delta == 1000).count() == 1
