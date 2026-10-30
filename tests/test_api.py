"""End-to-end tests through the HTTP layer."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

H = "/households/flat-1"


@pytest.fixture
def client() -> TestClient:
    # Fresh app (and fresh in-memory store) per test; seeded with flat-1 = asha, ben, chitra.
    return TestClient(create_app(seed=True))


def post_expense(client, paid_by, amount_paise, split_between, household=H):
    return client.post(
        f"{household}/expenses",
        json={"paid_by": paid_by, "amount_paise": amount_paise, "split_between": split_between},
    )


# ---------- the required example from the brief ----------

def test_required_case_end_to_end(client):
    assert post_expense(client, "asha", 90000, ["asha", "ben", "chitra"]).status_code == 201
    assert post_expense(client, "ben", 100000, ["asha", "ben", "chitra"]).status_code == 201
    assert post_expense(client, "chitra", 20000, ["ben", "chitra"]).status_code == 201

    body = client.get(f"{H}/balances").json()
    assert body["balances"] == {"asha": 26666, "ben": 26667, "chitra": -53333}
    assert body["suggested_payments"] == [
        {"from": "chitra", "to": "asha", "amount_paise": 26666},
        {"from": "chitra", "to": "ben", "amount_paise": 26667},
    ]

    # Paying the suggestions settles everyone.
    for p in body["suggested_payments"]:
        assert client.post(f"{H}/settlements", json=p).status_code == 201
    body = client.get(f"{H}/balances").json()
    assert body["balances"] == {"asha": 0, "ben": 0, "chitra": 0}
    assert body["suggested_payments"] == []


def test_settlement_example_from_brief(client):
    post_expense(client, "asha", 90000, ["asha", "ben", "chitra"])
    r = client.post(f"{H}/settlements", json={"from": "chitra", "to": "asha", "amount_paise": 26666})
    assert r.status_code == 201
    assert r.json() == {"from": "chitra", "to": "asha", "amount_paise": 26666}
    assert client.get(f"{H}/balances").json()["balances"] == {
        "asha": 33334, "ben": -30000, "chitra": -3334,
    }


# ---------- households ----------

def test_create_household_and_use_it(client):
    r = client.post("/households", json={"id": "flat-2", "members": ["dev", "esha"]})
    assert r.status_code == 201
    post_expense(client, "dev", 500, ["dev", "esha"], household="/households/flat-2")
    assert client.get("/households/flat-2/balances").json()["balances"] == {"dev": 250, "esha": -250}


def test_households_are_isolated(client):
    client.post("/households", json={"id": "flat-2", "members": ["asha", "ben"]})
    post_expense(client, "asha", 1000, ["asha", "ben"], household="/households/flat-2")
    assert client.get(f"{H}/balances").json()["balances"] == {"asha": 0, "ben": 0, "chitra": 0}


def test_duplicate_household_is_409(client):
    r = client.post("/households", json={"id": "flat-1", "members": ["x"]})
    assert r.status_code == 409


def test_household_with_duplicate_members_is_rejected(client):
    r = client.post("/households", json={"id": "flat-9", "members": ["x", "x"]})
    assert r.status_code == 422
    assert "unique" in r.json()["error"]


def test_unknown_household_is_404(client):
    assert client.get("/households/nope/balances").status_code == 404
    r = post_expense(client, "asha", 100, ["asha"], household="/households/nope")
    assert r.status_code == 404
    assert "nope" in r.json()["error"]


# ---------- bad expense input ----------

@pytest.mark.parametrize("amount", [0, -1, -90000])
def test_expense_rejects_zero_or_negative_amount(client, amount):
    r = post_expense(client, "asha", amount, ["asha", "ben"])
    assert r.status_code == 422
    assert "amount_paise" in r.json()["error"]


@pytest.mark.parametrize("amount", [900.5, "90000", True, None])
def test_expense_rejects_non_integer_amount(client, amount):
    r = post_expense(client, "asha", amount, ["asha", "ben"])
    assert r.status_code == 422
    assert "amount_paise" in r.json()["error"]


def test_expense_rejects_empty_split(client):
    r = post_expense(client, "asha", 100, [])
    assert r.status_code == 422
    assert "split_between" in r.json()["error"]


def test_expense_rejects_duplicate_in_split(client):
    r = post_expense(client, "asha", 100, ["asha", "asha"])
    assert r.status_code == 422


def test_expense_rejects_unknown_payer(client):
    r = post_expense(client, "zara", 100, ["asha"])
    assert r.status_code == 422
    assert "'zara'" in r.json()["error"]


def test_expense_rejects_unknown_member_in_split(client):
    r = post_expense(client, "asha", 100, ["asha", "zara", "yuki"])
    assert r.status_code == 422
    assert "'yuki'" in r.json()["error"] and "'zara'" in r.json()["error"]


def test_expense_rejects_missing_fields(client):
    r = client.post(f"{H}/expenses", json={"paid_by": "asha"})
    assert r.status_code == 422
    assert "amount_paise" in r.json()["error"]
    assert "split_between" in r.json()["error"]


def test_expense_rejects_malformed_json(client):
    r = client.post(f"{H}/expenses", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_rejected_expense_is_not_recorded(client):
    post_expense(client, "asha", 100, ["asha", "zara"])
    assert client.get(f"{H}/balances").json()["balances"] == {"asha": 0, "ben": 0, "chitra": 0}


# ---------- bad settlement input ----------

@pytest.mark.parametrize("amount", [0, -5, 1.5])
def test_settlement_rejects_bad_amount(client, amount):
    r = client.post(f"{H}/settlements", json={"from": "ben", "to": "asha", "amount_paise": amount})
    assert r.status_code == 422


def test_settlement_rejects_unknown_member(client):
    r = client.post(f"{H}/settlements", json={"from": "zara", "to": "asha", "amount_paise": 100})
    assert r.status_code == 422
    assert "'zara'" in r.json()["error"]


def test_settlement_rejects_paying_yourself(client):
    r = client.post(f"{H}/settlements", json={"from": "asha", "to": "asha", "amount_paise": 100})
    assert r.status_code == 422
    assert "themselves" in r.json()["error"]


# ---------- misc ----------

def test_balances_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Halfsies" in r.text
