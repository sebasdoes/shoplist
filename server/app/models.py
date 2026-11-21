"""T017 — pydantic schemas matching contracts/api.md."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_NAME = 200
MAX_QUANTITY = 50
MAX_NOTE = 200


class UpsertItem(BaseModel):
    """Contract mutation item: id required; other fields are an optional field-set
    (only present fields are applied — per-field LWW, research D2)."""

    model_config = {"extra": "forbid"}

    id: str
    name: str | None = None
    quantity: str | None = None
    note: str | None = None
    staple: bool | None = None
    tombstone: bool | None = None

    @field_validator("id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        try:
            uuid.UUID(value)
        except ValueError:
            raise ValueError("id must be a uuid") from None
        return value

    @field_validator("name")
    @classmethod
    def _name_valid(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not 1 <= len(trimmed) <= MAX_NAME:
            # data-model validation: name required after trim, 1–200 chars
            raise ValueError("name required after trim, 1–200 chars")
        return trimmed

    @field_validator("quantity")
    @classmethod
    def _quantity_valid(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_QUANTITY:
            raise ValueError("quantity max 50 chars")
        return value

    @field_validator("note")
    @classmethod
    def _note_valid(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_NOTE:
            raise ValueError("note max 200 chars")
        return value

    def provided_fields(self) -> set[str]:
        """The field-set present on the wire (pydantic fields_set)."""
        return set(self.model_fields_set) - {"id"}


class TripDecision(BaseModel):
    model_config = {"extra": "forbid"}

    item_id: str | None = None
    outcome: Literal["bought", "kept", "not_bought"]

    @field_validator("item_id")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            uuid.UUID(value)
        except ValueError:
            raise ValueError("item_id must be a uuid") from None
        return value

    @model_validator(mode="after")
    def _bought_needs_item_id(self) -> "TripDecision":
        if self.outcome == "bought" and self.item_id is None:
            # data-model transitions: bought applies to one ticked item
            raise ValueError("bought decision requires item_id")
        return self


class TripMutation(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    decisions: list[TripDecision] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        if not value:
            raise ValueError("trip id required")
        try:
            uuid.UUID(value)
        except ValueError:
            # data-model validation: trip id non-empty uuid format
            raise ValueError("trip id must be a uuid") from None
        return value


class Mutation(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}

    op: Literal["upsert_item", "trip"]
    item: UpsertItem | None = None
    trip: TripMutation | None = None

    @model_validator(mode="after")
    def _payload_matches_op(self) -> "Mutation":
        if self.op == "upsert_item" and self.item is None:
            raise ValueError("upsert_item requires item")
        if self.op == "trip" and self.trip is None:
            raise ValueError("trip requires trip")
        return self


class PushRequest(BaseModel):
    model_config = {"extra": "forbid"}

    mutations: list[Mutation] = Field(default_factory=list)


class Applied(BaseModel):
    id: str
    rev: int
    updated_at: str
    merged_into: str | None = None


class Readded(BaseModel):
    id: str
    name: str


class TripResponse(BaseModel):
    id: str
    duplicate: bool
    readded: list[Readded] = Field(default_factory=list)


class PushResponse(BaseModel):
    applied: list[Applied] = Field(default_factory=list)
    trip: TripResponse | None = None


class PullRequest(BaseModel):
    model_config = {"extra": "forbid"}

    watermark: int = Field(default=0, ge=0)
    include_history: bool = True


class ItemOut(BaseModel):
    id: str
    name: str
    quantity: str | None
    note: str | None
    status: Literal["active", "completed", "dropped"]
    staple: bool
    tombstone: bool
    created_by: str
    rev: int


class HistoryOut(BaseModel):
    id: str
    item_id: str | None
    name: str
    quantity: str | None
    note: str | None
    outcome: Literal["bought", "not_bought"]
    staple_snapshot: bool
    actor: str
    occurred_at: str
    rev: int


class PullResponse(BaseModel):
    watermark: int
    items: list[ItemOut] = Field(default_factory=list)
    history: list[HistoryOut] = Field(default_factory=list)
    full_snapshot: bool = False


def _row_to_item(row: Any) -> ItemOut:
    return ItemOut(
        id=row["id"],
        name=row["name"],
        quantity=row["quantity"],
        note=row["note"],
        status=row["status"],
        staple=bool(row["staple"]),
        tombstone=bool(row["tombstone"]),
        created_by=row["created_by"],
        rev=row["rev"],
    )


def _row_to_history(row: Any) -> HistoryOut:
    return HistoryOut(
        id=row["id"],
        item_id=row["item_id"],
        name=row["name"],
        quantity=row["quantity"],
        note=row["note"],
        outcome=row["outcome"],
        staple_snapshot=bool(row["staple_snapshot"]),
        actor=row["actor"],
        occurred_at=row["occurred_at"],
        rev=row["rev"],
    )
