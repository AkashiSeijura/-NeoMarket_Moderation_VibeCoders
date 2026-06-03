from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from main import app, repository


client = TestClient(app)
SOFT_REASON_ID = "a7b8c9d0-1234-5678-ef01-890123456789"
HARD_REASON_ID = "b4c5d6e7-8901-2345-5678-567890123456"


class FakeB2BResponse:
    def raise_for_status(self) -> None:
        return None


@pytest.fixture(autouse=True)
def clean_store():
    repository.reset()
    yield
    repository.reset()


@pytest.fixture()
def b2b_requests(monkeypatch):
    requests = []

    def fake_post(url, json, headers, timeout):
        requests.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeB2BResponse()

    monkeypatch.setattr("main.httpx.post", fake_post)
    return requests


def auth_headers(moderator_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {moderator_id}"}


def product_snapshot(product_id: str, sku_id: str | None = None) -> dict:
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
                "id": sku_id or str(uuid4()),
                "name": "base",
                "price": 1000,
                "active_quantity": 3,
            }
        ],
    }


def create_in_review_card(moderator_id: str, product_id: str | None = None, sku_id: str | None = None) -> str:
    product_id = product_id or str(uuid4())
    repository.create_test_card(
        product_id=product_id,
        seller_id=str(uuid4()),
        status_value="IN_REVIEW",
        json_after=product_snapshot(product_id, sku_id=sku_id),
        moderator_id=moderator_id,
        date_moderation=datetime.now(UTC).isoformat(),
    )
    return product_id


def decline_payload(**overrides) -> dict:
    payload = {
        "blocking_reason_id": SOFT_REASON_ID,
        "moderator_comment": "Описание и фото не соответствуют товару",
        "field_reports": [
            {
                "field_name": "description",
                "sku_id": None,
                "comment": "Текст описания скопирован с другого товара",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_soft_block_transitions_to_blocked_with_field_reports(b2b_requests):
    moderator_id = str(uuid4())
    sku_id = str(uuid4())
    product_id = create_in_review_card(moderator_id, sku_id=sku_id)
    payload = decline_payload(
        field_reports=[
            {
                "field_name": "description",
                "sku_id": None,
                "comment": "Текст описания скопирован с другого товара",
            },
            {
                "field_name": "sku_price",
                "sku_id": sku_id,
                "comment": "Цена подозрительно низкая",
            },
        ]
    )

    response = client.post(
        f"/api/v1/products/{product_id}/decline",
        json=payload,
        headers=auth_headers(moderator_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["product_id"] == product_id
    assert body["status"] == "BLOCKED"
    assert body["hard_block"] is False
    assert body["blocking_reason"]["id"] == SOFT_REASON_ID
    assert body["blocking_reason"]["comment"] == payload["moderator_comment"]
    assert body["field_reports"] == payload["field_reports"]

    card = repository.get_card(product_id)
    assert card["status"] == "BLOCKED"
    assert card["blocking_reason_id"] == SOFT_REASON_ID
    assert card["moderator_comment"] == payload["moderator_comment"]
    assert repository.count_field_reports(product_id) == 2
    assert len(b2b_requests) == 1


def test_soft_block_emits_event_to_b2b(b2b_requests):
    moderator_id = str(uuid4())
    product_id = create_in_review_card(moderator_id)
    payload = decline_payload()

    response = client.post(
        f"/api/v1/products/{product_id}/decline",
        json=payload,
        headers=auth_headers(moderator_id),
    )

    assert response.status_code == 200
    assert len(b2b_requests) == 1
    request = b2b_requests[0]
    assert request["url"] == "http://b2b:8000/api/v1/moderation/events"
    assert request["headers"]["X-Service-Key"] == "dev-moderation-to-b2b-key"
    assert request["json"]["product_id"] == product_id
    assert request["json"]["event_type"] == "BLOCKED"
    assert request["json"]["hard_block"] is False
    assert request["json"]["blocking_reason_id"] == SOFT_REASON_ID
    assert request["json"]["moderator_comment"] == payload["moderator_comment"]
    assert request["json"]["field_reports"] == payload["field_reports"]


def test_soft_block_unknown_reason_returns_400(b2b_requests):
    moderator_id = str(uuid4())
    product_id = create_in_review_card(moderator_id)

    response = client.post(
        f"/api/v1/products/{product_id}/decline",
        json=decline_payload(blocking_reason_id=str(uuid4())),
        headers=auth_headers(moderator_id),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "BLOCKING_REASON_NOT_FOUND"
    assert repository.get_card(product_id)["status"] == "IN_REVIEW"
    assert b2b_requests == []


def test_soft_block_others_card_returns_403(b2b_requests):
    owner_moderator_id = str(uuid4())
    other_moderator_id = str(uuid4())
    product_id = create_in_review_card(owner_moderator_id)

    response = client.post(
        f"/api/v1/products/{product_id}/decline",
        json=decline_payload(),
        headers=auth_headers(other_moderator_id),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "NOT_ASSIGNED_TO_YOU"
    assert repository.get_card(product_id)["status"] == "IN_REVIEW"
    assert b2b_requests == []


def test_soft_block_invalid_field_name_returns_400(b2b_requests):
    moderator_id = str(uuid4())
    product_id = create_in_review_card(moderator_id)

    response = client.post(
        f"/api/v1/products/{product_id}/decline",
        json=decline_payload(
            field_reports=[
                {
                    "field_name": "unknown_field",
                    "sku_id": None,
                    "comment": "Недопустимое поле",
                }
            ]
        ),
        headers=auth_headers(moderator_id),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_FIELD_NAME"
    assert repository.get_card(product_id)["status"] == "IN_REVIEW"
    assert b2b_requests == []


def test_soft_block_hard_only_reason_returns_400_or_routes_to_hard(b2b_requests):
    moderator_id = str(uuid4())
    product_id = create_in_review_card(moderator_id)

    response = client.post(
        f"/api/v1/products/{product_id}/decline",
        json=decline_payload(blocking_reason_id=HARD_REASON_ID),
        headers=auth_headers(moderator_id),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "HARD_BLOCK_REASON_NOT_ALLOWED"
    assert repository.get_card(product_id)["status"] == "IN_REVIEW"
    assert b2b_requests == []


def test_soft_block_openapi_ticket_block_path_accepts_field_path_reports(b2b_requests):
    moderator_id = str(uuid4())
    product_id = create_in_review_card(moderator_id)
    ticket_id = repository.get_card(product_id)["id"]

    response = client.post(
        f"/api/v1/tickets/{ticket_id}/block",
        json={
            "blocking_reason_ids": [SOFT_REASON_ID],
            "comment": "Фото низкого качества",
            "field_reports": [
                {
                    "field_path": "images[0].url",
                    "message": "Товар плохо виден",
                    "severity": "ERROR",
                }
            ],
        },
        headers=auth_headers(moderator_id),
    )

    assert response.status_code == 200
    assert response.json()["field_reports"] == [
        {
            "field_name": "product_images",
            "sku_id": None,
            "comment": "Товар плохо виден",
        }
    ]
    assert repository.get_card(product_id)["status"] == "BLOCKED"
