"""Unit tests for the pure money logic (no HTTP)."""

import itertools
import random

import pytest

from app.ledger import (
    Expense,
    Payment,
    Settlement,
    compute_balances,
    split_equally,
    suggest_payments,
)

MEMBERS = ["asha", "ben", "chitra"]


# ---------- split_equally ----------

def test_split_divides_evenly():
    assert split_equally(90000, MEMBERS) == {"asha": 30000, "ben": 30000, "chitra": 30000}


def test_split_leftover_goes_to_first_listed():
    assert split_equally(100000, MEMBERS) == {"asha": 33334, "ben": 33333, "chitra": 33333}


def test_split_leftover_follows_list_order_not_alphabetical():
    assert split_equally(100, ["chitra", "asha", "ben"]) == {"chitra": 34, "asha": 33, "ben": 33}


def test_split_two_leftover_paise():
    assert split_equally(11, MEMBERS) == {"asha": 4, "ben": 4, "chitra": 3}


def test_split_amount_smaller_than_people():
    assert split_equally(1, MEMBERS) == {"asha": 1, "ben": 0, "chitra": 0}


def test_split_always_sums_to_total():
    for amount in range(1, 500):
        for n in range(1, 8):
            people = [f"p{i}" for i in range(n)]
            assert sum(split_equally(amount, people).values()) == amount


def test_split_with_nobody_raises():
    with pytest.raises(ValueError):
        split_equally(100, [])


# ---------- the required example from the brief ----------

REQUIRED_EXPENSES = [
    Expense("asha", 90000, ("asha", "ben", "chitra")),
    Expense("ben", 100000, ("asha", "ben", "chitra")),
    Expense("chitra", 20000, ("ben", "chitra")),
]


def test_required_case_balances():
    balances = compute_balances(MEMBERS, REQUIRED_EXPENSES, [])
    assert balances == {"asha": 26666, "ben": 26667, "chitra": -53333}


def test_required_case_suggested_payments():
    balances = compute_balances(MEMBERS, REQUIRED_EXPENSES, [])
    assert suggest_payments(balances) == [
        Payment("chitra", "asha", 26666),
        Payment("chitra", "ben", 26667),
    ]


def test_required_case_settles_to_zero_after_suggested_payments():
    balances = compute_balances(MEMBERS, REQUIRED_EXPENSES, [])
    settlements = [
        Settlement(p.from_member, p.to_member, p.amount_paise) for p in suggest_payments(balances)
    ]
    assert compute_balances(MEMBERS, REQUIRED_EXPENSES, settlements) == {m: 0 for m in MEMBERS}


# ---------- compute_balances ----------

def test_no_activity_means_everyone_at_zero():
    assert compute_balances(MEMBERS, [], []) == {"asha": 0, "ben": 0, "chitra": 0}


def test_payer_not_in_split_is_owed_full_amount():
    balances = compute_balances(MEMBERS, [Expense("asha", 1000, ("ben", "chitra"))], [])
    assert balances == {"asha": 1000, "ben": -500, "chitra": -500}


def test_expense_only_for_payer_changes_nothing():
    balances = compute_balances(MEMBERS, [Expense("asha", 1000, ("asha",))], [])
    assert balances == {"asha": 0, "ben": 0, "chitra": 0}


def test_settlement_moves_balances():
    balances = compute_balances(
        MEMBERS,
        [Expense("asha", 900, ("asha", "ben", "chitra"))],
        [Settlement("ben", "asha", 300)],
    )
    assert balances == {"asha": 300, "ben": 0, "chitra": -300}


def test_partial_settlement():
    balances = compute_balances(
        MEMBERS,
        [Expense("asha", 900, ("asha", "ben", "chitra"))],
        [Settlement("ben", "asha", 100)],
    )
    assert balances == {"asha": 500, "ben": -200, "chitra": -300}


# ---------- suggest_payments ----------

def test_no_payments_when_all_settled():
    assert suggest_payments({"asha": 0, "ben": 0}) == []


def test_one_debtor_many_creditors():
    payments = suggest_payments({"a": 100, "b": 200, "c": -300})
    assert payments == [Payment("c", "a", 100), Payment("c", "b", 200)]


def test_many_debtors_one_creditor():
    payments = suggest_payments({"a": 300, "b": -100, "c": -200})
    assert payments == [Payment("b", "a", 100), Payment("c", "a", 200)]


def test_rejects_balances_that_do_not_sum_to_zero():
    with pytest.raises(ValueError):
        suggest_payments({"a": 100, "b": -99})


def _check_payment_rules(balances: dict[str, int]) -> None:
    payments = suggest_payments(balances)

    # Rule 4a: at most (members - 1) payments.
    assert len(payments) <= max(len(balances) - 1, 0)

    # Rule 4b: nobody both pays and receives.
    payers = {p.from_member for p in payments}
    receivers = {p.to_member for p in payments}
    assert payers.isdisjoint(receivers)

    # Every payment is positive and they settle everyone exactly.
    assert all(p.amount_paise > 0 for p in payments)
    after = dict(balances)
    for p in payments:
        after[p.from_member] += p.amount_paise
        after[p.to_member] -= p.amount_paise
    assert all(v == 0 for v in after.values())


def test_payment_rules_hold_for_random_households():
    rng = random.Random(42)  # fixed seed so failures are reproducible
    for _ in range(500):
        n = rng.randint(1, 8)
        members = [f"m{i}" for i in range(n)]
        expenses = [
            Expense(
                paid_by=rng.choice(members),
                amount_paise=rng.randint(1, 1_000_000),
                split_between=tuple(rng.sample(members, rng.randint(1, n))),
            )
            for _ in range(rng.randint(0, 10))
        ]
        _check_payment_rules(compute_balances(members, expenses, []))


def test_payment_rules_hold_for_all_small_balance_shapes():
    # Exhaustively check every 4-person balance vector in a small range that sums to zero.
    values = range(-3, 4)
    for a, b, c in itertools.product(values, repeat=3):
        d = -(a + b + c)
        _check_payment_rules({"a": a, "b": b, "c": c, "d": d})
