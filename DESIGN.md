# Design note: recording UPI payments

**Core idea:** create the payment row *before* the user leaves for UPI, keyed by our own ID. Webhooks, polls and reports only update that row through a state machine; balances change only on `SUCCEEDED`, which happens once.

## 1. Data per payment

- `payment_id`: our UUID, sent to the partner as the order reference.
- `client_request_id` (unique, from the app): a retried tap returns the same row.
- `household_id`, `from`, `to`, `amount_paise`, `status`, timestamps; fixed at creation.
- `partner_txn_id`, `upi_rrn` (unique): one bank transaction can't settle two rows.
- `payment_events`: append-only log of signature-verified webhooks and poll results.

## 2. States

```
CREATED ──► PENDING ──► SUCCEEDED  (terminal; writes the settlement)
   │           └──────► FAILED     (terminal)
   └──► EXPIRED  (only once the partner confirms no transaction exists)
```

Each transition is one conditional update (`WHERE status IN ('CREATED','PENDING')`) in the transaction that inserts the settlement. If no row changes, the payment was already final: reply `200 OK` and do nothing. So a **duplicate** or **10-minutes-late** webhook can't count twice. Nothing expires on a timer alone, or a late success would be lost.

## 3. What the user sees while it's unknown

- "Payment processing: we'll notify you when your bank confirms." The debt shows as *pending* and its **Pay button is disabled**, so nobody pays twice out of doubt.
- After ~15 min: "Taking longer than usual. Don't pay again; it will confirm or refund."

## 4. Catching missing webhooks

*Assumes the partner offers a status API and a daily report.*

1. **Poll job:** every minute, check unfinished payments over 2 minutes old, with backoff. Results use the webhook's transition code, so they can't double-count.
2. **Daily reconciliation** against the partner's report: paid there but not `SUCCEEDED` here gets succeeded; the reverse goes to an ops queue.
3. **Alert** if a payment is still unfinished after 24h.
