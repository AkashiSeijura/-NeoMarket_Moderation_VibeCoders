from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from main import DEFAULT_B2B_TO_MOD_KEY, app, repository


client = TestClient(app)
HEADERS = {"X-Service-Key": DEFAULT_B2B_TO_MOD_KEY}


@pytest.fixture(autouse=True)
def clean_store():
    repository.reset()
    yield
    repository.reset()


def product_snapshot(product_id: str, active_quantity: int = 3, title: str = "Phone") -> dict:
    return {
        "id": product_id,
        "title": title,
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
                "active_quantity": active_quantity,
                "cost_price": 500,
                "reserved_quantity": 1,
            }
        ],
    }


def b2b_event(event_type: str, payload: dict, idempotency_key: str | None = None) -> dict:
    return {
        "event_type": event_type,
        "idempotency_key": idempotency_key or str(uuid4()),
        "occurred_at": "2026-03-15T14:30:00Z",
        "payload": payload,
    }


def created_payload(product_id: str, seller_id: str, json_after: dict, **extra) -> dict:
    payload = {
        "product_id": product_id,
        "seller_id": seller_id,
        "category_id": str(uuid4()),
        "json_after": json_after,
    }
    payload.update(extra)
    return payload


def post_event(event: dict):
    return client.post("/api/v1/b2b/events", json=event, headers=HEADERS)


def test_created_pending():
    product_id = str(uuid4())
    seller_id = str(uuid4())

    response = post_event(
        b2b_event(
            "PRODUCT_CREATED",
            created_payload(product_id, seller_id, product_snapshot(product_id), queue_priority=1),
        )
    )

    assert response.status_code == 202
    card = repository.get_card(product_id)
    assert card["status"] == "PENDING"
    assert card["seller_id"] == seller_id
    assert card["json_before"] is None
    assert card["queue_priority"] == 1
    assert "cost_price" not in card["json_after"]["skus"][0]
    assert "reserved_quantity" not in card["json_after"]["skus"][0]


def test_edited_returns_to_review():
    product_id = str(uuid4())
    seller_id = str(uuid4())
    old_snapshot = product_snapshot(product_id, active_quantity=0, title="Old")
    new_snapshot = product_snapshot(product_id, active_quantity=5, title="New")
    repository.create_test_card(
        product_id=product_id,
        seller_id=seller_id,
        status_value="APPROVED",
        json_after=old_snapshot,
        queue_priority=1,
    )

    response = post_event(
        b2b_event(
            "PRODUCT_EDITED",
            created_payload(product_id, seller_id, new_snapshot),
        )
    )

    assert response.status_code == 202
    card = repository.get_card(product_id)
    assert card["status"] == "PENDING"
    assert card["queue_priority"] == 3
    assert card["json_before"]["title"] == "Old"
    assert card["json_after"]["title"] == "New"
    assert card["moderator_id"] is None


def test_edited_updates_in_review():
    product_id = str(uuid4())
    seller_id = str(uuid4())
    moderator_id = str(uuid4())
    old_snapshot = product_snapshot(product_id, title="Review old")
    new_snapshot = product_snapshot(product_id, title="Review new")
    repository.create_test_card(
        product_id=product_id,
        seller_id=seller_id,
        status_value="IN_REVIEW",
        json_after=old_snapshot,
        queue_priority=1,
        moderator_id=moderator_id,
    )
    repository.add_test_field_report(product_id)

    response = post_event(
        b2b_event(
            "PRODUCT_EDITED",
            created_payload(product_id, seller_id, new_snapshot),
        )
    )

    assert response.status_code == 202
    card = repository.get_card(product_id)
    assert card["status"] == "PENDING"
    assert card["queue_priority"] == 1
    assert card["moderator_id"] is None
    assert card["json_before"]["title"] == "Review old"
    assert card["json_after"]["title"] == "Review new"
    assert repository.count_field_reports(product_id) == 0


def test_deleted_archived():
    product_id = str(uuid4())
    seller_id = str(uuid4())
    repository.create_test_card(
        product_id=product_id,
        seller_id=seller_id,
        status_value="PENDING",
        json_after=product_snapshot(product_id),
    )

    response = post_event(
        b2b_event(
            "PRODUCT_DELETED",
            {"product_id": product_id},
        )
    )

    assert response.status_code == 202
    assert repository.get_card(product_id) is None


def test_duplicate_event_no_side_effects():
    product_id = str(uuid4())
    seller_id = str(uuid4())
    idempotency_key = str(uuid4())
    first_snapshot = product_snapshot(product_id, title="Original")
    changed_snapshot = product_snapshot(product_id, title="Changed")
    first_event = b2b_event(
        "PRODUCT_CREATED",
        created_payload(product_id, seller_id, first_snapshot, queue_priority=1),
        idempotency_key=idempotency_key,
    )
    duplicate_event = deepcopy(first_event)
    duplicate_event["payload"]["json_after"] = changed_snapshot
    duplicate_event["payload"]["queue_priority"] = 4

    first_response = post_event(first_event)
    second_response = post_event(duplicate_event)

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    assert second_response.json()["duplicate"] is True
    card = repository.get_card(product_id)
    assert card["json_after"]["title"] == "Original"
    assert card["queue_priority"] == 1


def test_missing_service_header_401():
    product_id = str(uuid4())
    seller_id = str(uuid4())

    response = client.post(
        "/api/v1/b2b/events",
        json=b2b_event(
            "PRODUCT_CREATED",
            created_payload(product_id, seller_id, product_snapshot(product_id)),
        ),
    )

    assert response.status_code == 401
    assert repository.get_card(product_id) is None
