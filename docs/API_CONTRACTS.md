# Bridge Client API Contracts

> Formalizes the dependency between Track D (Claim Page) and Track C (Bridge API).
> Track C must satisfy these contracts for seamless integration.

## Overview

The claim pipeline calls the Bridge API at step 8 (`RTC_LOCKED` transition) to lock native RTC on RustChain before wRTC is minted on the target chain.

---

## `POST /bridge/lock`

Lock RTC on the RustChain ledger and prepare for wRTC mint.

### Request

```json
{
  "wallet": "string — RustChain wallet name (e.g. 'alice-miner')",
  "amount": 100.0,
  "target_chain": "solana | base",
  "target_address": "string — Solana base58 pubkey or Base 0x address"
}
```

| Field            | Type   | Required | Description                          |
|------------------|--------|----------|--------------------------------------|
| `wallet`         | string | yes      | RTC wallet name on RustChain         |
| `amount`         | float  | yes      | RTC amount to lock (6 decimal max)   |
| `target_chain`   | string | yes      | `"solana"` or `"base"`               |
| `target_address` | string | yes      | Destination wallet on target chain   |

### Response (Success — 200)

```json
{
  "lock_id": "string — unique lock identifier",
  "status": "locked",
  "amount": 100.0,
  "target_chain": "solana",
  "target_address": "...",
  "locked_at": "2026-03-09T10:00:00Z",
  "tx_hash": "string — RustChain transaction hash (optional)"
}
```

### Response (Error — 4xx/5xx)

```json
{
  "error": "string — human-readable error message",
  "code": "INSUFFICIENT_BALANCE | WALLET_NOT_FOUND | RATE_LIMITED | INTERNAL_ERROR",
  "retry_after": 5
}
```

### Error Codes

| Code                   | HTTP | Description                              | Retryable |
|------------------------|------|------------------------------------------|-----------|
| `INSUFFICIENT_BALANCE` | 400  | Wallet has less RTC than requested       | No        |
| `WALLET_NOT_FOUND`     | 404  | RTC wallet does not exist                | No        |
| `RATE_LIMITED`          | 429  | Too many requests                        | Yes       |
| `INTERNAL_ERROR`       | 500  | Server-side failure                      | Yes       |

---

## `POST /bridge/release`

Release locked RTC back to the sender (reversal).

### Request

```json
{
  "lock_id": "string — lock identifier from /bridge/lock",
  "burn_tx_hash": "string — wRTC burn transaction hash on target chain"
}
```

### Response (Success — 200)

```json
{
  "lock_id": "...",
  "status": "released",
  "amount": 100.0,
  "released_at": "2026-03-09T12:00:00Z"
}
```

---

## Retry Semantics

- On `429 RATE_LIMITED`: wait `retry_after` seconds, then retry (max 3 attempts).
- On `500 INTERNAL_ERROR`: exponential backoff starting at 2s, max 3 attempts.
- All other errors are non-retryable and should transition the claim to `EXPIRED`.

## Idempotency

- `/bridge/lock` is **not** idempotent. Callers must check claim state before retrying.
- `/bridge/release` is idempotent — releasing the same `lock_id` twice returns the same response.

## Integration Test Contract

Track C must pass these assertions:

```python
# 1. Successful lock returns a lock_id
result = bridge.lock_rtc("test-wallet", 10.0, "solana", "valid_address")
assert "lock_id" in result
assert result["status"] == "locked"

# 2. Locking more than balance returns error
result = bridge.lock_rtc("empty-wallet", 99999.0, "solana", "valid_address")
assert "error" in result

# 3. Release returns the locked amount
result = bridge.release("lock_id_from_step_1", "burn_tx")
assert result["status"] == "released"
```

## Versioning

- Current version: `v1` (implicit, no URL prefix needed)
- Breaking changes increment the version: `POST /v2/bridge/lock`
- Track D will pin to `v1` until explicitly migrated
