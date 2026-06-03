from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from main import app, repository


client = TestClient(app)
SOFT_REASON_ID = "a7b8c9d0-1234-5678-ef01-890123456789"
HARD_REASON_ID = "b4c5d6e7-8901-2345-5678-567890123456"


@pytest.fixture(autouse=True)
def clean_store():
    repository.reset()
    yield
    repository.reset()


def auth_headers(moderator_id: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {moderator_id or uuid4()}"}


def product_snapshot(product_id: str) -> dict:
    return {
        "id": product_id,
        "title": "Phone",
        "description": "Product for moderation",
        "status": "ON_MODERATION",
        "deleted": False,
        "blocked": False,
        "category": {"id": str(uuid4()), "name": "Electronics"},
        "images": [{"url": "/s3/product.jpg", "ordering": 0}],
        "characteristics": [{"name": "Brand", "value": "Neo"}],
        "skus": [
            {
                "id": str(uuid4()),
                "name": "base",
                "price": 1000,
                "active_quantity": 3,
            }
        ],
    }


def test_list_returns_active_reasons():
    response = client.get("/api/v1/blocking-reasons")

    assert response.status_code == 200
    body = response.json()
    assert body
    assert all(reason["is_active"] is True for reason in body)
    assert {key for key in ("id", "title", "hard_block")} <= set(body[0])


def test_inactive_reasons_not_visible():
    repository.deactivate_blocking_reason(SOFT_REASON_ID)

    response = client.get("/api/v1/blocking-reasons")

    assert response.status_code == 200
    ids = {reason["id"] for reason in response.json()}
    assert SOFT_REASON_ID not in ids


def test_hard_block_filter_returns_matching_type():
    hard_response = client.get("/api/v1/blocking-reasons", params={"hard_block": "true"})
    soft_response = client.get("/api/v1/product-blocking-reasons", params={"hard_block": "false"})

    assert hard_response.status_code == 200
    assert soft_response.status_code == 200
    assert {reason["id"] for reason in hard_response.json()} == {HARD_REASON_ID} | {
        "c5d6e7f8-9012-3456-6789-678901234567",
        "d6e7f8a9-0123-4567-7890-789012345678",
    }
    assert all(reason["hard_block"] is True for reason in hard_response.json())
    assert all(reason["hard_block"] is False for reason in soft_response.json())


def test_referenced_reason_cannot_be_deleted():
    product_id = str(uuid4())
    repository.create_test_card(
        product_id=product_id,
        seller_id=str(uuid4()),
        status_value="BLOCKED",
        json_after=product_snapshot(product_id),
        blocking_reason_id=SOFT_REASON_ID,
    )

    response = client.delete(f"/api/v1/blocking-reasons/{SOFT_REASON_ID}", headers=auth_headers())

    assert response.status_code == 204
    card = repository.get_card(product_id)
    assert card["blocking_reason_id"] == SOFT_REASON_ID
    assert SOFT_REASON_ID not in {reason["id"] for reason in client.get("/api/v1/blocking-reasons").json()}
    assert SOFT_REASON_ID in {
        reason["id"]
        for reason in client.get("/api/v1/blocking-reasons", params={"is_active": "false"}).json()
    }
