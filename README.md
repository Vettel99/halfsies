# Halfsies: expense-splitting service

A small HTTP service that records shared expenses for a household, works out who owes whom, and suggests the fewest-ish payments to settle up. Python 3.11+ / FastAPI, data in memory.

The design note for Part 2 (UPI payments) is in [DESIGN.md](DESIGN.md).

## Run it (under 5 minutes)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

pytest -q                          # run the tests
uvicorn app.main:app --reload      # start the server on http://127.0.0.1:8000
```

Open http://127.0.0.1:8000 for a small balances page, or http://127.0.0.1:8000/docs for interactive API docs.

A household `flat-1` with members `asha`, `ben`, `chitra` is seeded on startup. Try the example from the brief:

```bash
curl -X POST localhost:8000/households/flat-1/expenses -H 'Content-Type: application/json' \
  -d '{"paid_by":"asha","amount_paise":90000,"split_between":["asha","ben","chitra"]}'
curl -X POST localhost:8000/households/flat-1/expenses -H 'Content-Type: application/json' \
  -d '{"paid_by":"ben","amount_paise":100000,"split_between":["asha","ben","chitra"]}'
curl -X POST localhost:8000/households/flat-1/expenses -H 'Content-Type: application/json' \
  -d '{"paid_by":"chitra","amount_paise":20000,"split_between":["ben","chitra"]}'
curl localhost:8000/households/flat-1/balances
```

## API

| Method & path | Body | Success |
|---|---|---|
| `POST /households` | `{"id": "flat-2", "members": ["dev", "esha"]}` | 201 |
| `GET /households/{id}` | | 200 |
| `POST /households/{id}/expenses` | `{"paid_by": "asha", "amount_paise": 90000, "split_between": ["asha", "ben", "chitra"]}` | 201: the expense plus `id` and `created_at` |
| `POST /households/{id}/settlements` | `{"from": "chitra", "to": "asha", "amount_paise": 26666}` | 201: the settlement plus `id` and `created_at` |
| `GET /households/{id}/balances` | | 200: `{"household_id", "balances": {member: paise}, "suggested_payments": [{"from", "to", "amount_paise"}]}` |

Both POST endpoints accept an optional **`Idempotency-Key`** header. Retrying with the same key and the same body returns the original record (same `id`, header `Idempotent-Replayed: true`) instead of recording it again:

```bash
curl -X POST localhost:8000/households/flat-1/settlements -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 7f3c-settle-1' -d '{"from":"chitra","to":"asha","amount_paise":26666}'
```

Errors are always `{"error": "<readable message>"}`:

- **422**: invalid body: amount zero/negative/over ₹10,00,000/not an integer, empty or duplicate `split_between`, unknown member, paying yourself, missing or unknown fields, malformed JSON, empty `Idempotency-Key`.
- **404**: unknown household.
- **409**: creating a household that already exists, or reusing an `Idempotency-Key` with a different body.

## Code layout

```
app/ledger.py   pure money logic: split_equally, compute_balances, suggest_payments
app/store.py    in-memory store, membership checks, idempotency
app/main.py     FastAPI routes, request validation, error handling
app/static/     optional balances page
tests/          test_ledger.py (unit + property-style), test_api.py (end-to-end over HTTP)
.github/        CI: runs the tests on Python 3.11 and 3.12 on every push
```

The money logic has no HTTP or storage dependencies, so most of the correctness tests hit it directly and the API tests focus on wiring and bad input.

## Decisions, trade-offs and assumptions

- **Integers in paise everywhere.** Amounts must be JSON integers; `900.5`, `"90000"` and `true` are rejected (pydantic `StrictInt`) rather than silently converted. The web page formats paise to rupees with integer maths, never floats.
- **Unknown fields are rejected, not ignored.** A body like `{"paid_by": "asha", "amount_rupees": 900, ...}` returns `amount_rupees: unknown field; amount_paise: Field required`. Silently dropping a field is how a typo or a rupees/paise mix-up turns into a wrong balance. *Trade-off:* clients must be updated in step with the API if a field is ever added, which is acceptable for an API that only the Halfsies app calls.
- **Amounts capped at ₹10,00,000 (10⁸ paise).** *Assumption:* no single flat expense or settlement is larger than that; a bigger number is almost certainly rupees typed as paise or an extra zero, and it would poison everyone's balance. The limit is one constant (`MAX_AMOUNT_PAISE`) if the product disagrees.
- **Optional idempotency keys on the POST endpoints.** Mobile clients retry on flaky networks; without a key, a retried "add ₹900 groceries" would be counted twice. With a key, the retry returns the original record. Keys are scoped per household and per endpoint; reusing one for a different body is a 409 rather than a silent mismatch; a request that fails validation doesn't use up its key, so the client can fix it and retry. *Trade-off:* the key is optional, because two identical expenses can be legitimate (two grocery runs). *Assumption:* keys live as long as the server process; with a database they'd expire after ~24h.
- **Balances are derived, not stored.** `GET /balances` replays all expenses and settlements each time. This is simple and can never drift out of sync with the history; at household scale (dozens of entries) the cost is negligible. With a real database I'd still keep the ledger as the source of truth and cache the balances if needed.
- **Leftover paise:** one each to the people listed first in `split_between`, in the order the client sent them (not alphabetical), as the brief says.
- **Settle-up algorithm:** greedy: repeatedly match the largest debtor with the largest creditor and pay the smaller of the two amounts. Each payment clears at least one person, so there are at most *n − 1* payments, and debtors only pay / creditors only receive. *Trade-off:* this isn't always the absolute minimum number of payments (that problem is NP-hard), but it meets the brief's bound, is easy to explain, and is deterministic (ties broken by name; output sorted by payer then payee). A randomised test plus an exhaustive test over small 4-person cases check both rules.
- **Payer need not be in the split.** *Assumption:* "Asha paid for Ben and Chitra's groceries" is a valid expense; Asha is then owed the full amount.
- **Duplicate names in `split_between` are rejected**, not merged. *Assumption:* it's almost certainly a client bug, and silently giving someone a double share would be a nasty surprise.
- **Settlements may exceed what's owed.** *Assumption:* in the real world people round up or prepay; the balance just flips sign and the suggestions adjust. Rejecting it would make "mark cash payment as done" fail for legitimate cases. Paying yourself is rejected.
- **Members are identified by name strings** that are unique within a household, matching the brief's examples. Real code would use user IDs.
- **Unknown member → 422, unknown household → 404.** The household is part of the URL (a resource that doesn't exist) whereas a member is a value in the body that fails validation.
- **In-memory store with a lock.** Fine for this exercise; restarting the server loses data. The `Store` class is the only thing that would change to use SQLite/Postgres.
- **No auth.** Out of scope for the time box; anyone can write to any household.
- **Pinned dependencies** in `requirements.txt` so the tests run the same on a reviewer's machine and in CI as they did on mine.

## With one more day

1. **SQLite persistence** with expenses and settlements as append-only rows, edit/delete implemented as reversing entries so history is auditable, and idempotency keys in a table with a unique constraint and an expiry.
2. **List endpoints** (`GET /expenses`, `GET /settlements`) and edit/delete of an expense.
3. **Unequal splits** (by shares, exact amounts, percentages), which real flatmates need for rent by room size.
4. Auth so members can only see and write to their own household; a members endpoint for invite links.

## AI tools used

<!-- Edit this section so it honestly reflects how you worked. -->
- **Claude Code (Claude Opus 5.5)**: used to scaffold the project, write the initial implementation and tests, and draft the README and design note. I reviewed every file, ran the tests, and checked the required example by hand (asha 90000 − 30000 − 33334 = +26666, etc.).
