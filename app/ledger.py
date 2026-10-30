"""Pure money logic: splitting expenses, computing balances, suggesting payments.

Nothing in here knows about HTTP or storage, so it can be tested directly.
All amounts are integers in paise.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Expense:
    paid_by: str
    amount_paise: int
    split_between: tuple[str, ...]


@dataclass(frozen=True)
class Settlement:
    from_member: str
    to_member: str
    amount_paise: int


@dataclass(frozen=True)
class Payment:
    from_member: str
    to_member: str
    amount_paise: int


def split_equally(amount_paise: int, people: list[str] | tuple[str, ...]) -> dict[str, int]:
    """Split an amount equally; leftover paise go one each to the first people listed.

    split_equally(100000, ["asha", "ben", "chitra"])
      -> {"asha": 33334, "ben": 33333, "chitra": 33333}
    """
    if not people:
        raise ValueError("cannot split between nobody")
    base, leftover = divmod(amount_paise, len(people))
    return {person: base + (1 if i < leftover else 0) for i, person in enumerate(people)}


def compute_balances(
    members: list[str],
    expenses: list[Expense],
    settlements: list[Settlement],
) -> dict[str, int]:
    """Net balance per member. Positive = is owed money, negative = owes money."""
    balances = {m: 0 for m in members}

    for expense in expenses:
        # The payer fronted the full amount...
        balances[expense.paid_by] += expense.amount_paise
        # ...and each participant consumed their share.
        for person, share in split_equally(expense.amount_paise, expense.split_between).items():
            balances[person] -= share

    for s in settlements:
        # Paying someone reduces your debt (balance goes up) and
        # reduces what they're owed (their balance goes down).
        balances[s.from_member] += s.amount_paise
        balances[s.to_member] -= s.amount_paise

    return balances


def suggest_payments(balances: dict[str, int]) -> list[Payment]:
    """Greedy settle-up: repeatedly match the biggest debtor with the biggest creditor.

    Guarantees:
      * Every payment fully clears at least one person, and the final payment
        clears two, so there are at most (people with non-zero balance - 1)
        payments, which is <= (members - 1).
      * Debtors only ever pay and creditors only ever receive, so nobody
        both pays and receives.

    Ties are broken by name so the output is deterministic. The returned list
    is sorted by (from, to) for stable display.
    """
    if sum(balances.values()) != 0:
        # Should be impossible: every expense/settlement moves money between members.
        raise ValueError("balances do not sum to zero")

    creditors = [[amount, name] for name, amount in balances.items() if amount > 0]
    debtors = [[-amount, name] for name, amount in balances.items() if amount < 0]

    payments: list[Payment] = []
    while creditors and debtors:
        creditors.sort(key=lambda c: (-c[0], c[1]))
        debtors.sort(key=lambda d: (-d[0], d[1]))
        creditor, debtor = creditors[0], debtors[0]

        amount = min(creditor[0], debtor[0])
        payments.append(Payment(from_member=debtor[1], to_member=creditor[1], amount_paise=amount))

        creditor[0] -= amount
        debtor[0] -= amount
        if creditor[0] == 0:
            creditors.pop(0)
        if debtor[0] == 0:
            debtors.pop(0)

    return sorted(payments, key=lambda p: (p.from_member, p.to_member))
