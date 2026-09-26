"""Step 39-c acceptance: the industry periods as the API serves them.

    DATABASE_URL=... python scripts/report_industry_classifications.py > log/step-39-c-report.json

Reads through the API itself (in process, read-only), so what is measured is
what a client receives. Prints one JSON document:

- `chain`: every listing span's chain checked (ADR-0030 §6): each change starts
  from the category the one before left, the last one ends in the anchor, and
  each period's category existed on its market then.
- `requester`: the six checks of `stock-model-selection`'s request
  (docs/requests/industry-classifications.md §7).
- `otc_agreement`: every stored TPEx by-category quote against the period in
  effect that day, each difference classified.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi.testclient import TestClient

from stock_data_center import api
from stock_data_center.v2 import industry
from stock_data_center.v2 import visibility as vis
from stock_data_center.v2.release_rules import industry_announcement_available_from

KEY = "report"
TAIPEI = ZoneInfo("Asia/Taipei")
NEW_2023 = set(industry.NEW_IN_2023)
# TPEx's by-category quotes applied its 2021 notice (effective 2021-06-01) only
# on 2021-06-10 (Step 39-b report): its ten companies differ on those days.
TPEX_2021_LAG = (date(2021, 6, 1), date(2021, 6, 10))
CATEGORIES_PUBLIC = industry_announcement_available_from(industry.CATEGORIES_ANNOUNCED_2023)


def main() -> None:
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    app = api.create_app(api_key=KEY, connect=lambda: api.read_only(engine), git_commit="report")
    client = TestClient(app)

    def get(path, **params):
        response = client.get(path, params=params, headers={"X-API-Key": KEY})
        response.raise_for_status()
        return response.json()

    def periods(**params):
        return get("/v1/datasets/industry-classifications", **params)["rows"]

    everything = periods(start="2020-01-02", end="2026-12-31")
    stocks = {r["stock_id"]: r for r in get("/v1/stocks")["rows"]}

    # ------------------------------------------------------------ chains
    with engine.connect() as c:
        inputs = vis.industry_inputs(c, vis.SystemPIT(vis.FOREVER))
        observed = defaultdict(dict)
        for stock, day, code in c.execute(sa.text(
                "SELECT DISTINCT ON (stock_id, trade_date) stock_id, trade_date, industry_code "
                "FROM industry_observations WHERE source = 'tpex_otc_quotes' "
                "ORDER BY stock_id, trade_date, recorded_at DESC")):
            observed[day][stock] = code
        lag = {stock for stock, _ in c.execute(sa.text(
            "SELECT stock_id, effective_date FROM industry_changes "
            "WHERE source = 'tpex_announcement' AND effective_date = :d"),
            {"d": TPEX_2021_LAG[0]})}
    issues, outside = {}, {}
    for span, changes, anchor in inputs:
        for found in vis.industry_chain_issues(span, changes, anchor):
            # A change before the span began: the company joined the universe
            # later (an innovation-board company moving to the main board).
            target = outside if " is outside the span " in found else issues
            target.setdefault(f"{span.stock_id}/{span.market}", []).append(found)
    no_period = sorted(f"{span.stock_id}/{span.market}" for span, changes, anchor in inputs
                       if not vis.industry_periods_of(span, changes, anchor,
                                                      vis.SystemPIT(vis.FOREVER)))
    chain = {"spans": len(inputs), "with_changes": sum(bool(ch) for _, ch, _ in inputs),
             "periods": len(everything), "issues": issues, "outside_span": outside,
             "no_period": no_period,
             "by_basis": dict(Counter(r["basis"] for r in everything)),
             "by_source": dict(Counter(r["source"] for r in everything))}

    # ------------------------------------------------------------ the requester's checks
    by_stock = defaultdict(list)
    for r in everything:
        by_stock[r["stock_id"]].append(r)
    new_today = {s for s, r in stocks.items()
                 if r["market"] and industry.code_of(r["industry"] or "") in NEW_2023}
    before = {s: [p for p in by_stock[s] if p["effective_from"] < "2023-07-03"]
              for s in new_today}
    check1 = {
        "stocks_in_35_38_today": len(new_today),
        "with_a_period_before_2023_07_03": sum(bool(v) for v in before.values()),
        "listed_on_or_after_2023_07_03": sorted(s for s, v in before.items() if not v),
        "in_35_38_before_2023_07_03": sorted(s for s, v in before.items()
                                             if any(p["industry_code"] in NEW_2023 for p in v)),
    }

    def on(day: str, stock: str) -> list:
        return [(p["market"], p["industry_code"], p["industry_name"])
                for p in periods(date=day, stock_id=stock)]

    check2 = {"2888@2025-07-23": on("2025-07-23", "2888"), "2888@2025-07-24": on("2025-07-24", "2888"),
              "2867@2026-08-31": on("2026-08-31", "2867"), "2867@2026-09-01": on("2026-09-01", "2867")}
    check3 = {day: on(day, "2448") for day in ("2020-01-02", "2020-12-31")}
    june = periods(date="2023-06-30")
    counts = Counter((p["market"], p["industry_code"]) for p in june)
    quoted = Counter(observed[date(2023, 6, 30)].values())
    unquoted = sorted(p["stock_id"] for p in june if p["market"] == "otc"
                      and p["stock_id"] not in observed[date(2023, 6, 30)])
    check4 = {
        "otc_categories": len({c for m, c in counts if m == "otc"} | set(quoted)),
        "otc_periods_not_quoted_that_day": [
            (s, next(p["industry_code"] for p in june if p["stock_id"] == s
                     and p["market"] == "otc")) for s in unquoted],
        "otc_counts_differ": {code: {"periods": counts[("otc", code)], "quotes": quoted[code]}
                              for code in sorted({c for m, c in counts if m == "otc"}
                                                 | set(quoted))
                              if counts[("otc", code)] != quoted[code]},
        "sii": "no source gives the listed categories' members on a past date: TWSE's "
               "by-category quotes are rebuilt under today's categories",
    }
    open_periods = [p for p in everything if p["effective_to"] is None]
    check5 = {
        "open_periods": len(open_periods),
        "differ_from_stocks_industry": sorted(
            (p["stock_id"], p["industry_name"], stocks[p["stock_id"]]["industry"])
            for p in open_periods if p["industry_name"] != stocks[p["stock_id"]]["industry"]),
        "listed_stocks_without_an_open_period": sorted(
            s for s, r in stocks.items() if r["market"] is not None
            and not any(p["effective_to"] is None and p["market"] == r["market"]
                        for p in by_stock[s])),
    }
    instants = sorted({datetime.fromisoformat(p["available_at"]) for p in everything})
    probes = sorted({*(t + d for t in instants for d in (timedelta(seconds=-1), timedelta())),
                     datetime(2020, 1, 1, tzinfo=TAIPEI), datetime.now(UTC)})
    late, leaked = 0, 0
    public_changes = sorted((datetime.fromisoformat(p["available_at"]), p["stock_id"],
                             p["market"], p["effective_from"]) for p in everything
                            if p["basis"] in ("change", "rename"))
    for t in probes:
        seen = periods(start="2020-01-02", end="2026-12-31", information_as_of=t.isoformat())
        late += sum(datetime.fromisoformat(p["available_at"]) > t for p in seen)
        known = {(s, m, f) for at, s, m, f in public_changes if at <= t}
        # A period ends only at a change public by t, a delisting that has come,
        # or the change of the categories themselves once its notice is public.
        leaked += sum(1 for p in seen if p["effective_to"] is not None
                      and (p["stock_id"], p["market"], p["effective_to"]) not in known
                      and p["effective_to"] > t.astimezone(TAIPEI).date().isoformat()
                      and not (p["effective_to"] == str(industry.RECLASSIFIED_2023)
                               and t >= CATEGORIES_PUBLIC))
    check6 = {"instants_probed": len(probes), "later_than_information_as_of": late,
              "ends_not_yet_public": leaked}

    # ------------------------------------------------------------ OTC agreement
    agreement = {}
    for day in sorted(observed):
        seen = {p["stock_id"]: p["industry_code"] for p in periods(date=str(day))
                if p["market"] == "otc"}
        result, differences = Counter(), []
        for stock, code in sorted(observed[day].items()):
            if stock not in seen:
                result["no_period"] += 1
                differences.append({"stock_id": stock, "quoted": code, "period": None})
            elif seen[stock] == code:
                result["agree"] += 1
            elif stock in lag and TPEX_2021_LAG[0] <= day < TPEX_2021_LAG[1]:
                result["tpex_2021_lag"] += 1
            else:
                result["differ"] += 1
                differences.append({"stock_id": stock, "quoted": code, "period": seen[stock]})
        agreement[str(day)] = {**result, "differences": differences}

    print(json.dumps({"chain": chain,
                      "requester": {"1": check1, "2": check2, "3": check3, "4": check4,
                                    "5": check5, "6": check6},
                      "otc_agreement": agreement}, ensure_ascii=False, default=str, indent=1))
    engine.dispose()


if __name__ == "__main__":
    main()
