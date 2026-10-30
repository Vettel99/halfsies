"""In-memory storage for households, expenses and settlements.

Membership checks live here (not in the request models) because they need
to know which household the request is for.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.ledger import Expense, Settlement


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class InvalidInput(Exception):
    pass


@dataclass(frozen=True)
class ExpenseRecord:
    id: str
    created_at: datetime
    expense: Expense


@dataclass(frozen=True)
class SettlementRecord:
    id: str
    created_at: datetime
    settlement: Settlement


@dataclass
class Household:
    id: str
    members: list[str]
    expenses: list[ExpenseRecord] = field(default_factory=list)
    settlements: list[SettlementRecord] = field(default_factory=list)
    # (kind, idempotency key) -> (the request it was first used with, the record it created)
    idempotency: dict[tuple[str, str], tuple[object, object]] = field(default_factory=dict)


class Store:
    def __init__(self) -> None:
        self._households: dict[str, Household] = {}
        # FastAPI runs sync endpoints in a thread pool, so guard writes.
        self._lock = threading.Lock()

    def create_household(self, household_id: str, members: list[str]) -> Household:
        with self._lock:
            if household_id in self._households:
                raise Conflict(f"household '{household_id}' already exists")
            household = Household(id=household_id, members=list(members))
            self._households[household_id] = household
            return household

    def get_household(self, household_id: str) -> Household:
        household = self._households.get(household_id)
        if household is None:
            raise NotFound(f"household '{household_id}' not found")
        return household

    def add_expense(
        self, household_id: str, expense: Expense, idempotency_key: str | None = None
    ) -> tuple[ExpenseRecord, bool]:
        """Record an expense. Returns (record, replayed); replayed is True when the
        idempotency key was seen before and the original record is returned instead."""
        with self._lock:
            household = self.get_household(household_id)
            replay = _find_replay(household, "expense", idempotency_key, expense)
            if replay is not None:
                return replay, True

            _require_members(household, [expense.paid_by, *expense.split_between])
            record = ExpenseRecord(id=_new_id("exp"), created_at=_now(), expense=expense)
            household.expenses.append(record)
            _remember(household, "expense", idempotency_key, expense, record)
            return record, False

    def add_settlement(
        self, household_id: str, settlement: Settlement, idempotency_key: str | None = None
    ) -> tuple[SettlementRecord, bool]:
        """Record a settlement. Same idempotency behaviour as add_expense."""
        with self._lock:
            household = self.get_household(household_id)
            replay = _find_replay(household, "settlement", idempotency_key, settlement)
            if replay is not None:
                return replay, True

            _require_members(household, [settlement.from_member, settlement.to_member])
            record = SettlementRecord(id=_new_id("stl"), created_at=_now(), settlement=settlement)
            household.settlements.append(record)
            _remember(household, "settlement", idempotency_key, settlement, record)
            return record, False


def _find_replay(household: Household, kind: str, key: str | None, request: object):
    """Return the record previously created with this key, or None if the key is new.

    Reusing a key for a *different* request is a client bug, so it's rejected
    rather than silently returning a record that doesn't match what was sent.
    """
    if key is None or (kind, key) not in household.idempotency:
        return None
    original_request, record = household.idempotency[(kind, key)]
    if original_request != request:
        raise Conflict(f"Idempotency-Key '{key}' was already used for a different {kind}")
    return record


def _remember(household: Household, kind: str, key: str | None, request: object, record: object) -> None:
    if key is not None:
        household.idempotency[(kind, key)] = (request, record)


def _require_members(household: Household, names: list[str]) -> None:
    unknown = sorted({n for n in names if n not in household.members})
    if unknown:
        raise InvalidInput(
            f"unknown member(s) {', '.join(repr(n) for n in unknown)} "
            f"in household '{household.id}'"
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)
