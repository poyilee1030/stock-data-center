# TDCC Shareholding Distribution

One source, `tdcc_opendata`: OpenData `getOD.ashx?id=1-5`, one weekly
whole-market file of every security's shareholding distribution. The measured
source facts are in `docs/source_field_audit.md` §4.9; the storage design is
ADR-0027 "35-c 定案".

The distribution is the observed input of the canonical
`shareholding_concentration:v1` dataset (Step 26). Large/mid/small-holder
ratios, counts, spreads and week-over-week changes are computed on demand from
it and inherit its visibility.

## Storage

`shareholding_distributions` holds one wide, append-only row per
`(stock_id, source, snapshot_date)`; a changed distribution for the same week is
a later row, and the earlier one stays. The seventeen levels of the
`tdcc-opendata-v1` profile, a code constant in `stock_data_center.v2.shareholding`,
become its columns:

| Level | Columns | Contract |
| --- | --- | --- |
| 1–15 | `holders_N`, `shares_N`, `percent_N` | holding range; shares and percent non-negative, percent at most 100 |
| 16 | `adjustment_shares`, `adjustment_percent` | 差異數調整; signed; no holder count |
| 17 | `total_holders`, `total_shares`, `total_percent` | the published 合計, stored as published |

Percentages have two decimals and at most three integer digits: the measured
range over 2020–2026 is −35.90 to 135.00.

**Level 16 is a signed difference, and it has no holders.** TDCC's own 說明4
defines it: 「差異數調整」項係指因資料日前1營業日客戶帳戶賣出
餘額不足之情事發生，使各「持股分級」合計股數與發行公司已發行股份總數產生之差異。
It is a reconciliation difference, not a holding band, and it is subtracted:
`合計股數 = Σ 分級1..15 股數 − 分級16 股數` holds for all 1,377,971
security-weeks in the archive, with `Σ + 16` holding for none. The OpenData bulk
file publishes the magnitude unsigned while the publisher's own portal renders
the same row negative, so the adapter stores it negative and a file that states
the sign itself is passed through. The bulk file sometimes states a small
integer as the level's holder count, which nothing official defines; it stays in
the raw file and is not stored.

**The total is stored as published.** TDCC publishes 合計 above 100 for some
securities — 158 rows of the archive, over 74 data dates, up to `135.00` — and a
table that cannot store what the source published is not an option. The
ceiling that does hold is per level: a holding level above 100% is refused.

## Reading a file

The week is keyed on the file's own 資料日期, never on its filename: two
archived files named for 2020-06-19 hold the 2020-06-12 table, and trusting the
name would invent a week and hide a missing one. A file whose content date is
not the week that was asked for is refused.

- **Only today's common stocks are written.** TDCC reports custody for codes
  well outside the universe; the rest are counted, never stored, and never
  added to `stocks` (ADR-0026).
- **A stock whose rows do not form a distribution is refused alone.** The
  week's other stocks are complete published facts, so they are written, and
  the fetch records the refused stock and its reason.
- **A truncated payload is reported, not smoothed.** The archived
  `2023/20231020.7z` is a download cut at 1.5 MiB, 562 securities short. Its
  complete securities are stored and the partial one is refused (owner
  decision, 2026-09-22). No endpoint can refetch it.

## History and forward capture

OpenData serves only the latest week, under one resource key, so a backfill run
fetches it once and has no periods to walk. The 375 archived weeks before the
first forward capture are the same endpoint's bytes, saved whole by the legacy
scraper; Step 35-c-1 copied their v1 history into `shareholding_distributions`.
They are the only copy: no official endpoint serves them again (ROADMAP §26.1).

## Publication

`snapshot_date` is the date the holdings describe, not a publication time. A
week becomes public at release rule `tdcc_weekly@1`: 12:00 Asia/Taipei on the
Sunday after the data date (`stock_data_center.v2.release_rules`). The rule
instant is computed on read, not stored; a later, different row for the same
week is public from its own `recorded_at`.

## Which weeks exist

TDCC compiles on its own business day, which over the 376 archived weeks means
330 Fridays, 22 Thursdays, 14 make-up Saturdays the exchange never opened, 9
Wednesdays and one Tuesday — and two weeks that carry two data dates each
(ADR-0025, audit §4.9). The expectation is therefore a week, not a date: every
ISO week holding at least one TWSE trading day should hold a snapshot.

Two absences are different things: a week the exchange never opened is not
expected (five in the v1 window, all Lunar New Year), and a week that had
trading days and holds no snapshot is a real gap. The converse also happens:
2021-W06 holds a snapshot (2021-02-09) although the exchange was shut all week.
