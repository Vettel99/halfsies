"""HTTP layer for Halfsies. Run with: uvicorn app.main:app --reload"""

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator

from app.ledger import Expense, Settlement, compute_balances, suggest_payments
from app.store import Conflict, ExpenseRecord, InvalidInput, NotFound, SettlementRecord, Store

# Assumption: no single household expense or settlement exceeds ₹10,00,000.
# Anything larger is almost certainly a typo (e.g. rupees entered as paise).
MAX_AMOUNT_PAISE = 10_00_000 * 100

MemberName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
# StrictInt rejects 900.5, "900" and true; gt=0 rejects zero and negatives.
PositivePaise = Annotated[StrictInt, Field(gt=0, le=MAX_AMOUNT_PAISE)]
# Optional header so a client can safely retry a POST without recording it twice.
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)]

SEED_HOUSEHOLDS = {"flat-1": ["asha", "ben", "chitra"]}


# ---------- request models ----------

class HouseholdIn(BaseModel):
    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    members: list[MemberName] = Field(min_length=1)

    @field_validator("members")
    @classmethod
    def members_unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("members must be unique")
        return v


class ExpenseIn(BaseModel):
    paid_by: MemberName
    amount_paise: PositivePaise
    split_between: list[MemberName]

    @field_validator("split_between")
    @classmethod
    def split_not_empty_and_unique(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("split_between must name at least one member")
        if len(set(v)) != len(v):
            raise ValueError("split_between must not list the same member twice")
        return v


class SettlementIn(BaseModel):
    # "from" is a Python keyword, so map it via an alias.
    model_config = ConfigDict(populate_by_name=True)

    from_member: MemberName = Field(alias="from")
    to_member: MemberName = Field(alias="to")
    amount_paise: PositivePaise


# ---------- app ----------

def create_app(seed: bool = True) -> FastAPI:
    app = FastAPI(title="Halfsies")
    store = Store()
    if seed:
        for household_id, members in SEED_HOUSEHOLDS.items():
            store.create_household(household_id, members)

    # --- error handling: always return {"error": "<readable message>"} ---

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": _format_validation_errors(exc)})

    @app.exception_handler(InvalidInput)
    async def on_invalid(_: Request, exc: InvalidInput) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": str(exc)})

    @app.exception_handler(NotFound)
    async def on_not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.exception_handler(Conflict)
    async def on_conflict(_: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": str(exc)})

    # --- routes ---

    @app.post("/households", status_code=201)
    def create_household(body: HouseholdIn) -> dict:
        household = store.create_household(body.id, body.members)
        return {"id": household.id, "members": household.members}

    @app.get("/households/{household_id}")
    def get_household(household_id: str) -> dict:
        household = store.get_household(household_id)
        return {"id": household.id, "members": household.members}

    @app.post("/households/{household_id}/expenses", status_code=201)
    def add_expense(
        household_id: str,
        body: ExpenseIn,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> dict:
        record, replayed = store.add_expense(
            household_id,
            Expense(
                paid_by=body.paid_by,
                amount_paise=body.amount_paise,
                split_between=tuple(body.split_between),
            ),
            idempotency_key,
        )
        if replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return _expense_json(record)

    @app.post("/households/{household_id}/settlements", status_code=201)
    def add_settlement(
        household_id: str,
        body: SettlementIn,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> dict:
        if body.from_member == body.to_member:
            raise InvalidInput("a member cannot pay themselves")
        record, replayed = store.add_settlement(
            household_id,
            Settlement(
                from_member=body.from_member,
                to_member=body.to_member,
                amount_paise=body.amount_paise,
            ),
            idempotency_key,
        )
        if replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return _settlement_json(record)

    @app.get("/households/{household_id}/balances")
    def get_balances(household_id: str) -> dict:
        household = store.get_household(household_id)
        balances = compute_balances(
            household.members,
            [r.expense for r in household.expenses],
            [r.settlement for r in household.settlements],
        )
        return {
            "household_id": household.id,
            "balances": balances,
            "suggested_payments": [
                {"from": p.from_member, "to": p.to_member, "amount_paise": p.amount_paise}
                for p in suggest_payments(balances)
            ],
        }

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    return app


def _expense_json(record: ExpenseRecord) -> dict:
    return {
        "id": record.id,
        "created_at": record.created_at.isoformat(),
        "paid_by": record.expense.paid_by,
        "amount_paise": record.expense.amount_paise,
        "split_between": list(record.expense.split_between),
    }


def _settlement_json(record: SettlementRecord) -> dict:
    return {
        "id": record.id,
        "created_at": record.created_at.isoformat(),
        "from": record.settlement.from_member,
        "to": record.settlement.to_member,
        "amount_paise": record.settlement.amount_paise,
    }


def _format_validation_errors(exc: RequestValidationError) -> str:
    """Turn pydantic's error list into one human-readable sentence."""
    messages = []
    for err in exc.errors():
        # loc looks like ("body", "amount_paise") or ("body", "split_between", 0)
        field = ".".join(str(part) for part in err["loc"] if part != "body")
        msg = err["msg"].removeprefix("Value error, ")
        messages.append(f"{field}: {msg}" if field else msg)
    return "; ".join(messages)


app = create_app()
