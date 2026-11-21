"""T017 — pydantic schemas matching contracts/api.md."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_NAME = 200
MAX_QUANTITY = 50
MAX_NOTE = 200
MAX_LIST_NAME = 100
# specs/002 data-model: fixed default list uuid seeded by the migration
DEFAULT_LIST_ID = "00000000-0000-4000-8000-000000000001"


def _uuid_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        uuid.UUID(value)
    except ValueError:
        raise ValueError("must be a uuid") from None
    return value


class UpsertItem(BaseModel):
    """Contract mutation item: id required; other fields are an optional field-set
    (only present fields are applied — per-field LWW, research D2). v2: every
    item MUST carry list_id (uuid of a live list)."""

    model_config = {"extra": "forbid"}

    id: str
    list_id: str
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

    @field_validator("list_id")
    @classmethod
    def _list_uuid(cls, value: str) -> str:
        _uuid_or_none(value)
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
    list_id: str  # v2: trips are scoped to one list; cross-list decisions → 400
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

    @field_validator("list_id")
    @classmethod
    def _list_uuid(cls, value: str) -> str:
        _uuid_or_none(value)
        return value


class ListPayload(BaseModel):
    """create_list payload: {id, name} — name trimmed 1–100, NOT unique."""

    model_config = {"extra": "forbid"}

    id: str
    name: str

    @field_validator("id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        try:
            uuid.UUID(value)
        except ValueError:
            raise ValueError("list id must be a uuid") from None
        return value

    @field_validator("name")
    @classmethod
    def _name_valid(cls, value: str) -> str:
        trimmed = value.strip()
        if not 1 <= len(trimmed) <= MAX_LIST_NAME:
            # contracts/api.md: list name required after trim, 1–100 chars
            raise ValueError("list name required after trim, 1–100 chars")
        return trimmed



class Mutation(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}

    op: Literal["upsert_item", "trip", "create_list", "delete_list", "reorder_lists"]
    item: UpsertItem | None = None
    trip: TripMutation | None = None
    list: ListPayload | None = None
    list_id: str | None = None
    # Sequence, NOT `list[...]`: the sibling field named `list` (bound to None)
    # shadows the builtin in this class namespace and breaks pydantic's eval.
    order: Sequence[str] | None = None

    @field_validator("list_id")
    @classmethod
    def _list_uuid(cls, value: str | None) -> str | None:
        return _uuid_or_none(value)

    @model_validator(mode="after")
    def _payload_matches_op(self) -> "Mutation":
        if self.op == "upsert_item" and self.item is None:
            raise ValueError("upsert_item requires item")
        if self.op == "trip" and self.trip is None:
            raise ValueError("trip requires trip")
        if self.op == "create_list" and self.list is None:
            raise ValueError("create_list requires list")
        if self.op == "delete_list" and self.list_id is None:
            raise ValueError("delete_list requires list_id")
        if self.op == "reorder_lists" and self.order is None:
            raise ValueError("reorder_lists requires order")
        return self


class PushRequest(BaseModel):
    model_config = {"extra": "forbid"}

    mutations: list[Mutation] = Field(default_factory=list)


class Applied(BaseModel):
    id: str
    rev: int
    updated_at: str
    merged_into: str | None = None


class AppliedList(BaseModel):
    """contracts/api.md v2 + v3: applied lists section — {id, name, rev,
    tombstone, sort_order}."""

    id: str
    name: str
    rev: int
    tombstone: bool
    sort_order: int = 0


class AppliedGroup(BaseModel):
    """Push response `applied` gains a `lists` section (T005) alongside items."""

    items: list[Applied] = Field(default_factory=list)
    lists: list[AppliedList] = Field(default_factory=list)


class Readded(BaseModel):
    id: str
    name: str


class TripResponse(BaseModel):
    id: str
    duplicate: bool
    readded: list[Readded] = Field(default_factory=list)


class PushResponse(BaseModel):
    applied: AppliedGroup = Field(default_factory=AppliedGroup)
    trip: TripResponse | None = None


class PullRequest(BaseModel):
    model_config = {"extra": "forbid"}

    watermark: int = Field(default=0, ge=0)
    include_history: bool = True


class ListOut(BaseModel):
    id: str
    name: str
    tombstone: bool
    rev: int
    sort_order: int = 0


class ItemOut(BaseModel):
    id: str
    list_id: str
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
    list_id: str
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
    lists: list[ListOut] = Field(default_factory=list)
    items: list[ItemOut] = Field(default_factory=list)
    history: list[HistoryOut] = Field(default_factory=list)
    full_snapshot: bool = False


def _row_to_item(row: Any) -> ItemOut:
    return ItemOut(
        id=row["id"],
        list_id=row["list_id"],
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
        list_id=row["list_id"],
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


def _row_to_list(row: Any) -> ListOut:
    return ListOut(
        id=row["id"],
        name=row["name"],
        tombstone=bool(row["tombstone"]),
        rev=row["rev"],
        sort_order=row["sort_order"],
    )
