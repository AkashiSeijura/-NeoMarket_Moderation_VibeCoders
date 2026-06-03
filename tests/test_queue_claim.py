from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from main import app, repository


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_store():
    repository.reset()
    yield
    repository.reset()


def auth_headers(moderator_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {moderator_id}"}


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
            }
        ],
    }


def create_card(
    *,
    product_id: str | None = None,
    seller_id: str | None = None,
    status_value: str = "PENDING",
    queue_priority: int = 1,
    date_created: str | None = None,
    moderator_id: str | None = None,
    date_moderation: str | None = None,
) -> str:
    product_id = product_id or str(uuid4())
    repository.create_test_card(
        product_id=product_id,
        seller_id=seller_id or str(uuid4()),
        status_value=status_value,
        json_after=product_snapshot(product_id),
        queue_priority=queue_priority,
        moderator_id=moderator_id,
        date_created=date_created,
        date_moderation=date_moderation,
    )
    return product_id


def test_next_returns_oldest_pending():
    moderator_id = str(uuid4())
    newer_product_id = create_card(date_created="2026-03-15T14:31:00+00:00")
    oldest_product_id = create_card(date_created="2026-03-15T14:30:00+00:00")

    response = client.post("/api/v1/queue/claim", headers=auth_headers(moderator_id))

    assert response.status_code == 200
    body = response.json()
    assert body["product_id"] == oldest_product_id
    assert body["status"] == "IN_REVIEW"
    assert body["assigned_moderator_id"] == moderator_id
    assert body["claimed_at"] is not None
    assert body["claim_expires_at"] is not None

    oldest_card = repository.get_card(oldest_product_id)
    newer_card = repository.get_card(newer_product_id)
    assert oldest_card["status"] == "IN_REVIEW"
    assert oldest_card["moderator_id"] == moderator_id
    assert newer_card["status"] == "PENDING"


def test_concurrent_two_moderators_get_different_cards():
    first_product_id = create_card(date_created="2026-03-15T14:30:00+00:00")
    second_product_id = create_card(date_created="2026-03-15T14:31:00+00:00")
    moderators = [str(uuid4()), str(uuid4())]

    def claim(moderator_id: str):
        local_client = TestClient(app)
        return local_client.post("/api/v1/queue/claim", headers=auth_headers(moderator_id))

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(claim, moderators))

    assert [response.status_code for response in responses] == [200, 200]
    claimed_product_ids = {response.json()["product_id"] for response in responses}
    assert claimed_product_ids == {first_product_id, second_product_id}
    assert len(claimed_product_ids) == 2

    cards = [repository.get_card(first_product_id), repository.get_card(second_product_id)]
    assert {card["moderator_id"] for card in cards} == set(moderators)
    assert {card["status"] for card in cards} == {"IN_REVIEW"}


def test_empty_queue_returns_204():
    response = client.post("/api/v1/queue/claim", headers=auth_headers(str(uuid4())))

    assert response.status_code == 204
    assert response.content == b""


def test_moderator_already_has_in_review_returns_409():
    moderator_id = str(uuid4())
    active_product_id = create_card(
        status_value="IN_REVIEW",
        moderator_id=moderator_id,
        date_moderation=datetime.now(UTC).isoformat(),
    )
    pending_product_id = create_card(status_value="PENDING")

    response = client.post("/api/v1/queue/claim", headers=auth_headers(moderator_id))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MODERATOR_ALREADY_HAS_IN_REVIEW"
    assert repository.get_card(active_product_id)["status"] == "IN_REVIEW"
    assert repository.get_card(pending_product_id)["status"] == "PENDING"


def test_expired_in_review_returns_to_queue():
    moderator_id = str(uuid4())
    stale_moderator_id = str(uuid4())
    stale_claimed_at = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
    stale_product_id = create_card(
        status_value="IN_REVIEW",
        moderator_id=stale_moderator_id,
        date_created="2026-03-15T14:30:00+00:00",
        date_moderation=stale_claimed_at,
    )

    response = client.post("/api/v1/queue/claim", headers=auth_headers(moderator_id))

    assert response.status_code == 200
    assert response.json()["product_id"] == stale_product_id
    card = repository.get_card(stale_product_id)
    assert card["status"] == "IN_REVIEW"
    assert card["moderator_id"] == moderator_id
