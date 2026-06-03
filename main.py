import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field


SERVICE_KEY_HEADER = "X-Service-Key"
DEFAULT_B2B_TO_MOD_KEY = "b2b-to-mod-key"
DEFAULT_DB_PATH = "moderation.sqlite3"
DEFAULT_IN_REVIEW_TIMEOUT_MINUTES = 30


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class IncomingB2BEvent(BaseModel):
    event_type: Literal["PRODUCT_CREATED", "PRODUCT_EDITED", "PRODUCT_DELETED"]
    idempotency_key: UUID
    occurred_at: datetime
    payload: dict[str, Any]


class CanonicalProductEvent(BaseModel):
    product_id: UUID
    seller_id: UUID | None = None
    event: Literal["CREATED", "EDITED", "DELETED"]
    date: datetime
    idempotency_key: UUID
    json_before: dict[str, Any] | None = None
    json_after: dict[str, Any] | None = None
    category_id: UUID | None = None
    queue_priority: int | None = Field(default=None, ge=1, le=4)


class ClaimQueueRequest(BaseModel):
    queue_priority: int | None = Field(default=None, ge=1, le=4)
    category_ids: list[UUID] | None = None


@dataclass(frozen=True)
class ProductEvent:
    event_type: str
    idempotency_key: str
    occurred_at: datetime
    product_id: str
    seller_id: str | None
    category_id: str | None
    queue_priority: int | None
    json_after: dict[str, Any] | None


class ProductEventRepository:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.getenv("MODERATION_DB_PATH", DEFAULT_DB_PATH)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS product_moderation (
                    id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL UNIQUE,
                    seller_id TEXT NOT NULL,
                    category_id TEXT,
                    status TEXT NOT NULL,
                    queue_priority INTEGER NOT NULL CHECK (queue_priority BETWEEN 1 AND 4),
                    json_before TEXT,
                    json_after TEXT NOT NULL,
                    blocking_reason_id TEXT,
                    moderator_id TEXT,
                    moderator_comment TEXT,
                    date_created TEXT NOT NULL,
                    date_updated TEXT NOT NULL,
                    date_moderation TEXT,
                    total_active_quantity INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS product_moderation_field_report (
                    id TEXT PRIMARY KEY,
                    product_moderation_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    sku_id TEXT,
                    comment TEXT NOT NULL,
                    date_created TEXT NOT NULL,
                    FOREIGN KEY (product_moderation_id)
                        REFERENCES product_moderation(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS processed_product_events (
                    idempotency_key TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    date_processed TEXT NOT NULL
                );
                """
            )

    def reset(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM product_moderation_field_report")
            connection.execute("DELETE FROM product_moderation")
            connection.execute("DELETE FROM processed_product_events")

    def get_card(self, product_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM product_moderation WHERE product_id = ?",
                (product_id,),
            ).fetchone()
        return self._card_from_row(row) if row else None

    def create_test_card(
        self,
        *,
        product_id: str,
        seller_id: str,
        status_value: str,
        json_after: dict[str, Any],
        queue_priority: int = 1,
        moderator_id: str | None = None,
        blocking_reason_id: str | None = None,
        date_created: str | None = None,
        date_moderation: str | None = None,
    ) -> None:
        now = now_iso()
        created_at = date_created or now
        clean_after = strip_private_fields(json_after)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO product_moderation (
                    id, product_id, seller_id, category_id, status, queue_priority,
                    json_before, json_after, blocking_reason_id, moderator_id,
                    moderator_comment, date_created, date_updated, date_moderation,
                    total_active_quantity
                )
                VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    product_id,
                    seller_id,
                    status_value,
                    queue_priority,
                    dump_json(clean_after),
                    blocking_reason_id,
                    moderator_id,
                    created_at,
                    now,
                    date_moderation,
                    total_active_quantity(clean_after),
                ),
            )

    def add_test_field_report(self, product_id: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM product_moderation WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Card not found")
            connection.execute(
                """
                INSERT INTO product_moderation_field_report (
                    id, product_moderation_id, field_name, sku_id, comment, date_created
                )
                VALUES (?, ?, 'title', NULL, 'bad title', ?)
                """,
                (str(uuid4()), row["id"], now_iso()),
            )

    def count_field_reports(self, product_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM product_moderation_field_report fr
                JOIN product_moderation pm ON pm.id = fr.product_moderation_id
                WHERE pm.product_id = ?
                """,
                (product_id,),
            ).fetchone()
        return int(row["total"])

    def process_event(self, event: ProductEvent) -> bool:
        with self.connect() as connection:
            if self._event_processed(connection, event.idempotency_key):
                return True

            card = connection.execute(
                "SELECT * FROM product_moderation WHERE product_id = ?",
                (event.product_id,),
            ).fetchone()

            if event.event_type == "PRODUCT_CREATED":
                self._process_created(connection, event, card)
            elif event.event_type == "PRODUCT_EDITED":
                self._process_edited(connection, event, card)
            elif event.event_type == "PRODUCT_DELETED":
                self._process_deleted(connection, event.product_id)
            else:
                raise business_error("UNKNOWN_EVENT", "Unsupported product event")

            connection.execute(
                """
                INSERT INTO processed_product_events (
                    idempotency_key, product_id, event_type, occurred_at, date_processed
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.idempotency_key,
                    event.product_id,
                    event.event_type,
                    event.occurred_at.isoformat(),
                    now_iso(),
                ),
            )
            return False

    def claim_next_card(
        self,
        *,
        moderator_id: str,
        queue_priority: int | None = None,
        category_ids: list[str] | None = None,
    ) -> dict[str, Any] | None:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._return_expired_reviews(connection)

            active_card = connection.execute(
                """
                SELECT 1
                FROM product_moderation
                WHERE status = 'IN_REVIEW' AND moderator_id = ?
                LIMIT 1
                """,
                (moderator_id,),
            ).fetchone()
            if active_card is not None:
                raise conflict_error(
                    "MODERATOR_ALREADY_HAS_IN_REVIEW",
                    "Moderator already has an active IN_REVIEW ticket",
                )

            where_parts = ["status = 'PENDING'"]
            params: list[Any] = []
            if queue_priority is not None:
                where_parts.append("queue_priority = ?")
                params.append(queue_priority)
            if category_ids:
                placeholders = ", ".join("?" for _ in category_ids)
                where_parts.append(f"category_id IN ({placeholders})")
                params.extend(category_ids)

            row = connection.execute(
                f"""
                SELECT *
                FROM product_moderation
                WHERE {" AND ".join(where_parts)}
                ORDER BY queue_priority ASC, date_created ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            now = now_iso()
            cursor = connection.execute(
                """
                UPDATE product_moderation
                SET status = 'IN_REVIEW',
                    moderator_id = ?,
                    date_moderation = ?,
                    date_updated = ?
                WHERE id = ? AND status = 'PENDING'
                """,
                (moderator_id, now, now, row["id"]),
            )
            if cursor.rowcount != 1:
                raise conflict_error("TICKET_ALREADY_CLAIMED", "Ticket was already claimed")

            claimed = connection.execute(
                "SELECT * FROM product_moderation WHERE id = ?",
                (row["id"],),
            ).fetchone()
            connection.commit()
            return self._ticket_response_from_row(claimed)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _return_expired_reviews(self, connection: sqlite3.Connection) -> None:
        cutoff = datetime.now(UTC) - timedelta(minutes=in_review_timeout_minutes())
        now = now_iso()
        connection.execute(
            """
            UPDATE product_moderation
            SET status = 'PENDING',
                moderator_id = NULL,
                date_moderation = NULL,
                date_updated = ?
            WHERE status = 'IN_REVIEW'
              AND date_moderation IS NOT NULL
              AND date_moderation <= ?
            """,
            (now, cutoff.isoformat()),
        )

    def _event_processed(self, connection: sqlite3.Connection, idempotency_key: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM processed_product_events WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return row is not None

    def _process_created(
        self,
        connection: sqlite3.Connection,
        event: ProductEvent,
        card: sqlite3.Row | None,
    ) -> None:
        if card and card["status"] == "HARD_BLOCKED":
            return
        if card:
            raise business_error("PRODUCT_ALREADY_EXISTS", "Duplicate PRODUCT_CREATED event")
        if event.seller_id is None or event.json_after is None:
            raise business_error("INVALID_EVENT_PAYLOAD", "PRODUCT_CREATED requires seller_id and json_after")

        now = now_iso()
        json_after = strip_private_fields(event.json_after)
        connection.execute(
            """
            INSERT INTO product_moderation (
                id, product_id, seller_id, category_id, status, queue_priority,
                json_before, json_after, blocking_reason_id, moderator_id,
                moderator_comment, date_created, date_updated, date_moderation,
                total_active_quantity
            )
            VALUES (?, ?, ?, ?, 'PENDING', ?, NULL, ?, NULL, NULL, NULL, ?, ?, NULL, ?)
            """,
            (
                str(uuid4()),
                event.product_id,
                event.seller_id,
                event.category_id,
                event.queue_priority or 3,
                dump_json(json_after),
                now,
                now,
                total_active_quantity(json_after),
            ),
        )

    def _process_edited(
        self,
        connection: sqlite3.Connection,
        event: ProductEvent,
        card: sqlite3.Row | None,
    ) -> None:
        if card is None:
            raise business_error("PRODUCT_NOT_FOUND", "PRODUCT_EDITED event references unknown product")
        if card["status"] == "HARD_BLOCKED":
            return
        if event.seller_id is None or event.json_after is None:
            raise business_error("INVALID_EVENT_PAYLOAD", "PRODUCT_EDITED requires seller_id and json_after")

        old_status = card["status"]
        json_before = load_json(card["json_after"])
        json_after = strip_private_fields(event.json_after)
        active_quantity = total_active_quantity(json_after)
        queue_priority = next_queue_priority(old_status, card["queue_priority"], active_quantity)

        connection.execute(
            """
            UPDATE product_moderation
            SET seller_id = ?,
                category_id = ?,
                status = 'PENDING',
                queue_priority = ?,
                json_before = ?,
                json_after = ?,
                moderator_id = NULL,
                date_updated = ?,
                total_active_quantity = ?
            WHERE product_id = ?
            """,
            (
                event.seller_id,
                event.category_id or card["category_id"],
                queue_priority,
                dump_json(json_before),
                dump_json(json_after),
                now_iso(),
                active_quantity,
                event.product_id,
            ),
        )
        connection.execute(
            """
            DELETE FROM product_moderation_field_report
            WHERE product_moderation_id = ?
            """,
            (card["id"],),
        )

    def _process_deleted(
        self,
        connection: sqlite3.Connection,
        product_id: str,
    ) -> None:
        connection.execute(
            "DELETE FROM product_moderation WHERE product_id = ?",
            (product_id,),
        )

    def _card_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "product_id": row["product_id"],
            "seller_id": row["seller_id"],
            "category_id": row["category_id"],
            "status": row["status"],
            "queue_priority": row["queue_priority"],
            "json_before": load_json(row["json_before"]) if row["json_before"] else None,
            "json_after": load_json(row["json_after"]),
            "blocking_reason_id": row["blocking_reason_id"],
            "moderator_id": row["moderator_id"],
            "moderator_comment": row["moderator_comment"],
            "date_created": row["date_created"],
            "date_updated": row["date_updated"],
            "date_moderation": row["date_moderation"],
            "total_active_quantity": row["total_active_quantity"],
        }

    def _ticket_response_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        claimed_at = row["date_moderation"]
        return {
            "id": row["id"],
            "product_id": row["product_id"],
            "seller_id": row["seller_id"],
            "category_id": row["category_id"],
            "kind": "EDIT" if row["json_before"] else "CREATE",
            "status": row["status"],
            "queue_priority": row["queue_priority"],
            "assigned_moderator_id": row["moderator_id"],
            "claimed_at": claimed_at,
            "claim_expires_at": claim_expires_at(claimed_at),
            "decision_at": None,
            "created_at": row["date_created"],
            "updated_at": row["date_updated"],
        }


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def in_review_timeout_minutes() -> int:
    raw_value = os.getenv("MODERATION_IN_REVIEW_TIMEOUT_MINUTES")
    if raw_value is None:
        return DEFAULT_IN_REVIEW_TIMEOUT_MINUTES
    return max(1, int(raw_value))


def claim_expires_at(claimed_at: str | None) -> str | None:
    if claimed_at is None:
        return None
    return (datetime.fromisoformat(claimed_at) + timedelta(minutes=in_review_timeout_minutes())).isoformat()


def dump_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(value: str) -> dict[str, Any]:
    return json.loads(value)


def strip_private_fields(product_data: dict[str, Any]) -> dict[str, Any]:
    clean_product = dict(product_data)
    clean_skus = []
    for sku in clean_product.get("skus", []):
        clean_sku = dict(sku)
        clean_sku.pop("cost_price", None)
        clean_sku.pop("reserved_quantity", None)
        clean_skus.append(clean_sku)
    clean_product["skus"] = clean_skus
    return clean_product


def total_active_quantity(product_data: dict[str, Any]) -> int:
    total = 0
    for sku in product_data.get("skus", []):
        total += int(sku.get("active_quantity", sku.get("activeQuantity", 0)) or 0)
    return total


def next_queue_priority(old_status: str, current_priority: int, active_quantity: int) -> int:
    if old_status == "BLOCKED":
        return 2
    if old_status in {"MODERATED", "APPROVED"}:
        return 3 if active_quantity > 0 else 4
    return current_priority


def business_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ErrorResponse(code=code, message=message).model_dump(),
    )


def conflict_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=ErrorResponse(code=code, message=message).model_dump(),
    )


def require_service_key(x_service_key: str | None) -> None:
    expected = os.getenv("B2B_TO_MOD_KEY", DEFAULT_B2B_TO_MOD_KEY)
    if x_service_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorResponse(
                code="UNAUTHORIZED",
                message=f"Missing or invalid {SERVICE_KEY_HEADER}",
            ).model_dump(),
        )


def require_moderator_id(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorResponse(code="UNAUTHORIZED", message="Missing bearer token").model_dump(),
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return str(UUID(token))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorResponse(code="UNAUTHORIZED", message="Invalid bearer token").model_dump(),
        ) from error


def parse_openapi_event(incoming: IncomingB2BEvent) -> ProductEvent:
    payload = incoming.payload
    return ProductEvent(
        event_type=incoming.event_type,
        idempotency_key=str(incoming.idempotency_key),
        occurred_at=incoming.occurred_at,
        product_id=str_required(payload, "product_id"),
        seller_id=str_optional(payload, "seller_id"),
        category_id=str_optional(payload, "category_id"),
        queue_priority=int_optional(payload, "queue_priority"),
        json_after=dict_optional(payload, "json_after"),
    )


def parse_canonical_event(incoming: CanonicalProductEvent) -> ProductEvent:
    event_type = {
        "CREATED": "PRODUCT_CREATED",
        "EDITED": "PRODUCT_EDITED",
        "DELETED": "PRODUCT_DELETED",
    }[incoming.event]
    default_priority = 1 if incoming.event == "CREATED" else incoming.queue_priority
    return ProductEvent(
        event_type=event_type,
        idempotency_key=str(incoming.idempotency_key),
        occurred_at=incoming.date,
        product_id=str(incoming.product_id),
        seller_id=str(incoming.seller_id) if incoming.seller_id else None,
        category_id=str(incoming.category_id) if incoming.category_id else None,
        queue_priority=default_priority,
        json_after=incoming.json_after,
    )


def str_required(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if value is None:
        raise business_error("INVALID_EVENT_PAYLOAD", f"{field_name} is required")
    return str(value)


def str_optional(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    return str(value) if value is not None else None


def int_optional(payload: dict[str, Any], field_name: str) -> int | None:
    value = payload.get(field_name)
    return int(value) if value is not None else None


def dict_optional(payload: dict[str, Any], field_name: str) -> dict[str, Any] | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise business_error("INVALID_EVENT_PAYLOAD", f"{field_name} must be an object")
    return value


repository = ProductEventRepository()
app = FastAPI(title="NeoMarket Moderation API")


@app.post("/api/v1/b2b/events", status_code=status.HTTP_202_ACCEPTED)
def receive_b2b_product_event(
    incoming: IncomingB2BEvent,
    response: Response,
    x_service_key: str | None = Header(default=None, alias=SERVICE_KEY_HEADER),
) -> dict[str, Any]:
    require_service_key(x_service_key)
    duplicate = repository.process_event(parse_openapi_event(incoming))
    response.status_code = status.HTTP_202_ACCEPTED
    return {"accepted": True, "duplicate": duplicate}


@app.post("/api/v1/events/product", status_code=status.HTTP_200_OK)
def receive_canonical_product_event(
    incoming: CanonicalProductEvent,
    x_service_key: str | None = Header(default=None, alias=SERVICE_KEY_HEADER),
) -> dict[str, Any]:
    require_service_key(x_service_key)
    duplicate = repository.process_event(parse_canonical_event(incoming))
    return {"accepted": True, "duplicate": duplicate}


@app.get("/api/v1/product-moderation/{product_id}")
def get_product_moderation(product_id: UUID) -> dict[str, Any]:
    card = repository.get_card(str(product_id))
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(code="NOT_FOUND", message="Product moderation card not found").model_dump(),
        )
    return card


@app.post("/api/v1/queue/claim", status_code=status.HTTP_200_OK)
def claim_next_queue_ticket(
    response: Response,
    request: ClaimQueueRequest | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any] | None:
    moderator_id = require_moderator_id(authorization)
    claimed = repository.claim_next_card(
        moderator_id=moderator_id,
        queue_priority=request.queue_priority if request else None,
        category_ids=[str(category_id) for category_id in request.category_ids] if request and request.category_ids else None,
    )
    if claimed is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    return claimed
