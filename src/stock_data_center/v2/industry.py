"""The exchanges' industry categories: codes, names and when each existed (Step 39, ADR-0030).

Small static configuration, so a code constant (CLAUDE.md §0). The codes are
the ISIN site's industry list (`isin.twse.com.tw/isin/class_i.jsp?kind=1`,
read 2026-09-26), shared by both markets: TWSE `MI_INDEX?type=` and TPEx
`afterTrading/otc?type=` select by the same codes, and the ISIN names are what
`stocks.industry` holds.

The announcements spell names their own way (「建材營造」 for 建材營造業,
「其他」 for 其他業, 「半導體」, 「生技醫療」), so a name is matched with its
trailing 業 dropped, and the few older names are listed outright.

Both exchanges changed the categories themselves on 2023-07-03 (TWSE
臺證上一字第1120004601號 and TPEx 證櫃監字第11200554281號, both 2023-03-28):
35–38 are new, 16 觀光事業 became 觀光餐旅, and TPEx merged 34 電子商務 into
36 and 18 貿易百貨 into 38. The companies that moved are each listed in that
year's reclassification announcements.
"""

from __future__ import annotations

from datetime import date

WINDOW_START = date(2020, 1, 2)
RECLASSIFIED_2023 = date(2023, 7, 3)
# The date of both exchanges' notices changing the categories themselves.
CATEGORIES_ANNOUNCED_2023 = date(2023, 3, 28)
MARKETS = ("sii", "otc")

# Today's ISIN names.
NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械",
    "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業",
    "12": "汽車工業", "13": "電子工業", "14": "建材營造業", "15": "航運業", "16": "觀光餐旅",
    "17": "金融保險業", "18": "貿易百貨業", "19": "綜合", "20": "其他業", "21": "化學工業",
    "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業",
    "26": "光電業", "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業",
    "30": "資訊服務業", "31": "其他電子業", "32": "文化創意業", "33": "農業科技業",
    # TPEx only, merged into 36 on 2023-07-03; no longer on the ISIN list.
    "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
}
# Names a code had before a rename: code -> [(until, name)], `until` exclusive.
FORMER_NAMES = {"16": [(RECLASSIFIED_2023, "觀光事業")]}
# Spellings no rule below derives.
ALIASES = {"觀光事業": "16", "電子商務業": "34", "金融業": "17"}
# When a category existed on a market: (from, until), either end open.
NEW_IN_2023 = ("35", "36", "37", "38")
EXISTED = {
    **{(code, market): (RECLASSIFIED_2023, None) for code in NEW_IN_2023 for market in MARKETS},
    ("34", "otc"): (None, RECLASSIFIED_2023),
    ("18", "otc"): (None, RECLASSIFIED_2023),
}
ONLY_ON = {"34": "otc"}


def _plain(name: str) -> str:
    return name.strip().removesuffix("業")


_BY_NAME = {
    **{_plain(name): code for code, name in NAMES.items()},
    **{_plain(name): code for code, names in FORMER_NAMES.items() for _, name in names},
    **{_plain(name): code for name, code in ALIASES.items()},
}


_SPELLINGS = frozenset({*NAMES.values(), *ALIASES,
                        *(name for names in FORMER_NAMES.values() for _, name in names)})


def begins_a_name(text: str) -> bool:
    """Whether some category's name, as any document spells it, starts with `text`."""
    text = text.strip()
    return bool(text) and any(name.startswith(text) for name in _SPELLINGS)


def code_of(name: str) -> str | None:
    """The code of a category as any exchange document spells it, or None."""
    return _BY_NAME.get(_plain(name)) if name.strip() else None


def name_of(code: str, on: date) -> str:
    """The category's name on a date."""
    for until, name in FORMER_NAMES.get(code, []):
        if on < until:
            return name
    return NAMES[code]


def exists(code: str, market: str, on: date) -> bool:
    """Whether the market had this category on a date."""
    if code not in NAMES or ONLY_ON.get(code, market) != market:
        return False
    since, until = EXISTED.get((code, market), (None, None))
    return (since is None or on >= since) and (until is None or on < until)


def existence(code: str, market: str) -> tuple[date | None, date | None]:
    """(from, until) the market had this category, either end open."""
    return EXISTED.get((code, market), (None, None))
