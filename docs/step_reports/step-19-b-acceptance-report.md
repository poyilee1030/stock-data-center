# Step 19-b Acceptance Report

Status: IN REVIEW

Scope: the TWSE result-feed adapters and their detail pages

Schema impact: none. Migration impact: none. PIT impact: none, because nothing
is written.
`src/` changed by +586/−11 lines.

The step adds three list adapters (`TWT49U`, `TWTAUU`, `TWTB8U`) and two
detail adapters (`TWT49UDetail`, `TWTAVUDetail`). It also restores the
row-plus-detail hook that 19-a left out: `CorporateActionRow.detail_request`,
`CorporateActionDetailRequest`, `ParsedCorporateActionDetail`, and
`observation(row, detail=None)` on the base adapter. A TPEx row refuses a
detail; a TWSE dividend or reduction row refuses to become an observation
without one.

## Baseline

Measured before 19-a and recorded in its report: all 6,182 rows of legacy
`dividend` (2020-01-02 → 2026-09-11) match TWT49U, with zero value
differences. TWT49U has 1,602 rows legacy never kept: 1,402 ETFs, 170
preferred shares and 30 TDRs. This step has to reproduce that result through
the adapter.

The code started from the draft that was cut out of 19-a before its split. It
was kept on the local branch `step-19-b-wip`, together with the TWSE fixtures
captured on 2026-09-16.

## Acceptance evidence

All numbers come from the per-year files fetched on 2026-09-16, parsed through
the adapters with `executed_through = 2026-09-15`.

| Criterion | Result | Evidence |
| --- | --- | --- |
| Zero duplicate `(code, locator)` over each TWSE feed's full history | PASS | `TWT49U`: 7,792 events, 35 not yet executed. `TWTAUU`: 149 events, 8 not yet executed, three of them with `-` for every price. `TWTB8U`: 10 events. All three feeds have zero duplicate keys and zero duplicate `(code, date)`. |
| Parsed list values equal legacy `dividend` | PASS | Legacy has 6,182 rows. Every one is found through the adapter and none is missing. Comparing close before, reference price, 權值+息值 and type gives **zero differences**. The window holds 7,784 official events, so 1,602 are new data. |
| `最近一次申報*` and the name never reach business content | PASS | Changing the name, or the three latest-filing columns, of a row leaves its completed observation equal. A test breaks the adapter by keeping those columns, and the suite fails. |
| A TWSE par-value change stores no share terms | PASS | All 10 events 2020-2026 map to `other`, with `old_shares` and `new_shares` NULL and their prices kept. |
| The mapping holds on real detail pages | PASS | 68 details sampled across 2020-2026 all map, and none is quarantined. The 53 `TWT49UDetail` pages cover 37 common-share and 16 preferred-share pages. The 15 `TWTAVUDetail` pages cover 8 cash-refund and 7 loss-offset reductions, including 3356, where a reduction was filed together with an ex-dividend. |

Mapping every detail page (about 7,800 requests) is 19-d's work; this step
proves the mapping on the samples above.

## Verification

```text
552 passed, 3 skipped
```

Baseline before this step: 513 collected. The 42 unit tests added here make up
the difference, and all of them failed at collection before the adapters
returned.

Most of the adapter code already existed in the draft, so failing at
collection proves little on its own. The suite was therefore also run
against 22 deliberate breaks of the module. Every TWSE-side break made a test
fail. The two that survived break TPEx guards, which 19-a's suite catches.
One break first survived:

- **The break:** removing the "TPEx rows take no detail" refusal.
- **Why the test missed it:** it expected a `ValueError`, and the identity
  check that fired instead raises `SourceDataError`, which is a subclass of
  `ValueError`.
- **The fix:** the test now uses a detail for the same security and checks the
  error message.

- `git diff --check`: clean. `alembic check`: no new operations.
- `ruff check`: nothing new against `main`. The unsorted `__all__` in
  `adapters/__init__.py` predates this branch.

## Scope exclusions confirmed

- No write, no retraction, no source policy, no CLI; that work is 19-c.
- No backfill and no fetch of every detail page; that work is 19-d.
- No ETF split feed; that work is 19-e.
- No share ratio is inferred from prices for a TWSE par-value change.
