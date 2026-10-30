"""In-memory storage for households, expenses and settlements.

Membership checks live here (not in the request models) because they need
to know which household the request is for.
"""

import threading
from dataclasses import dataclass, field

from app.ledger import Expense, Settlement


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class InvalidInput(Exception):
    pass


@dataclass
class Household:
    id: str
    members: list[str]
    expenses: list[Expense] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)


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

    def add_expense(self, household_id: str, expense: Expense) -> Expense:
        with self._lock:
            household = self.get_household(household_id)
            _require_members(household, [expense.paid_by, *expense.split_between])
            household.expenses.append(expense)
            return expense

    def add_settlement(self, household_id: str, settlement: Settlement) -> Settlement:
        with self._lock:
            household = self.get_household(household_id)
            _require_members(household, [settlement.from_member, settlement.to_member])
            household.settlements.append(settlement)
            return settlement


def _require_members(household: Household, names: list[str]) -> None:
    unknown = sorted({n for n in names if n not in household.members})
    if unknown:
        raise InvalidInput(
            f"unknown member(s) {', '.join(repr(n) for n in unknown)} "
            f"in household '{household.id}'"
        )
