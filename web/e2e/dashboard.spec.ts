// Step 37-a acceptance: every drawn value is the API's value (ROADMAP Step 37).
// The page's chart is read back from ECharts and compared, point by point over
// the whole history, with the same API answer fetched here with the same key.
import { expect, test, type Page } from "@playwright/test";

const KEY = process.env.STOCKDC_API_KEY ?? "";
const SHOTS = process.env.STOCKDC_SCREENSHOTS;
const TODAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(new Date());

async function apiRows(page: Page, path: string, params: Record<string, string>) {
  const qs = new URLSearchParams({ start: "2000-01-01", end: TODAY, ...params });
  const response = await page.request.get(`/v1/${path}?${qs}`, { headers: { "X-API-Key": KEY } });
  expect(response.status()).toBe(200);
  return response.json();
}

async function withKey(page: Page, theme: "dark" | "light" = "dark") {
  await page.addInitScript(([key, t]) => {
    localStorage.setItem("stockdc.apiKey", key);
    localStorage.setItem("stockdc.theme", t);
    localStorage.setItem("stockdc.mode", "raw");
  }, [KEY, theme]);
}

type Series = { id: string; data: unknown[]; markLine?: { data: { xAxis: string; label: { formatter: string } }[] } };

async function chartOption(page: Page) {
  await page.waitForFunction(() => {
    const chart = window.__stockdc?.charts.price;
    const series = chart?.getOption().series as { id: string; data: unknown[] }[] | undefined;
    return !!series?.find((s) => s.id === "candles")?.data.length;
  });
  return page.evaluate(() => {
    const option = window.__stockdc!.charts.price.getOption() as { series: unknown[]; xAxis: { data: string[] }[] };
    return { axis: option.xAxis[0].data, series: option.series as Series[] };
  });
}

function find(series: Series[], id: string): Series {
  const found = series.find((s) => s.id === id);
  if (!found) throw new Error(`no series ${id}`);
  return found;
}

function valueOf(point: unknown): unknown {
  return point !== null && typeof point === "object" && !Array.isArray(point) ? (point as { value: unknown }).value : point;
}

type Row = Record<string, unknown> & { trade_date: string; source: string };

function compareCandles(axis: string[], candles: Series, rows: Row[], fields: [string, string, string, string]) {
  const at = new Map(rows.map((r) => [r.trade_date, r]));
  // No row is dropped: every API date has a slot.
  for (const r of rows) expect(axis, `axis has ${r.trade_date}`).toContain(r.trade_date);
  let drawn = 0, empty = 0;
  axis.forEach((date, i) => {
    const row = at.get(date);
    const values = row ? fields.map((f) => row[f] as number | null) : null;
    if (values === null || values.some((v) => v === null)) {
      expect(candles.data[i], `${date} is empty`).toBe("-");
      empty++;
    } else {
      expect(candles.data[i], date).toEqual(values);
      drawn++;
    }
  });
  return { drawn, empty };
}

function compareLine(axis: string[], line: Series, rows: Row[], field: string) {
  const at = new Map(rows.map((r) => [r.trade_date, r]));
  axis.forEach((date, i) => {
    const expected = at.get(date)?.[field] ?? null;
    const drawn = valueOf(line.data[i]);
    if (expected === null) expect([null, "-", undefined], `${field} ${date}`).toContain(drawn);
    else expect(drawn, `${field} ${date}`).toBe(expected);
  });
}

const OHLC: [string, string, string, string] = ["open_price", "close_price", "low_price", "high_price"];
const ADJ: [string, string, string, string] = ["adjusted_open_price", "adjusted_close_price", "adjusted_low_price", "adjusted_high_price"];

test("the page asks for the key and serves no data without one", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("textbox", { name: "API key" }).fill(KEY);
  await page.getByRole("button", { name: "儲存" }).click();
  await page.getByTestId("home-search").fill("台積");
  await page.getByRole("option").first().click();
  await expect(page).toHaveURL(/#\/stock\/2330$/);
});

for (const stockId of ["2330", "6488"]) {
  test(`${stockId}: raw candles, volume, averages and indicators equal the API`, async ({ page }) => {
    await withKey(page);
    await page.goto(`/#/stock/${stockId}`);
    const prices = (await apiRows(page, "datasets/daily-prices", { stock_id: stockId })).rows as Row[];
    const indicators = (await apiRows(page, "datasets/technical-indicators", { stock_id: stockId })).rows as Row[];
    const { axis, series } = await chartOption(page);
    const counts = compareCandles(axis, find(series, "candles"), prices, OHLC);
    compareLine(axis, find(series, "volume"), prices, "volume");
    for (const w of [5, 20, 60]) compareLine(axis, find(series, `ma${w}`), indicators, `ma${w}`);
    compareLine(axis, find(series, "k"), indicators, "k");
    compareLine(axis, find(series, "d"), indicators, "d");
    console.log(`${stockId} raw: ${axis.length} trading days, ${counts.drawn} candles, ${counts.empty} empty, ${prices.length} API rows`);

    await page.getByRole("button", { name: "MA240" }).click();
    await page.getByRole("button", { name: "布林" }).click();
    await page.getByRole("radio", { name: "MACD" }).click();
    const more = await chartOption(page);
    compareLine(more.axis, find(more.series, "ma240"), indicators, "ma240");
    for (const f of ["bb_upper", "bb_middle", "bb_lower", "macd_dif", "macd_dea", "macd_hist"]) {
      compareLine(more.axis, find(more.series, f), indicators, f);
    }
    await page.getByRole("radio", { name: "RSI" }).click();
    const rsi = await chartOption(page);
    for (const f of ["rsi6", "rsi12"]) compareLine(rsi.axis, find(rsi.series, f), indicators, f);
  });

  test(`${stockId}: adjusted candles and their events equal the API`, async ({ page }) => {
    await withKey(page);
    await page.goto(`/#/stock/${stockId}`);
    await chartOption(page);
    await page.getByRole("radio", { name: "還原價" }).click();
    await expect(page.getByTestId("events")).toBeVisible();
    const source = (await apiRows(page, "datasets/daily-prices", { stock_id: stockId })).rows.at(-1).source;
    const answer = await apiRows(page, "datasets/adjusted-prices-pit", { stock_id: stockId, source });
    const { axis, series } = await chartOption(page);
    const candles = find(series, "candles");
    const counts = compareCandles(axis, candles, answer.rows, ADJ);
    const ids = series.map((s) => s.id);
    expect(ids.some((id) => id.startsWith("ma") || id.startsWith("bb_"))).toBe(false);
    const marked = candles.markLine!.data.map((d) => [d.xAxis, d.label.formatter]);
    expect(marked).toEqual(answer.events.map((e: { ex_date: string; event_type: string }) => [e.ex_date, e.event_type]));
    expect(await page.getByTestId("events").locator("tbody tr").count()).toBe(answer.events.length);
    console.log(`${stockId} adjusted: ${counts.drawn} candles, ${counts.empty} empty, ${answer.events.length} events`);
  });
}

test("5236: two price sources are shown apart, never merged", async ({ page }) => {
  await withKey(page);
  await page.goto("/#/stock/5236");
  const prices = (await apiRows(page, "datasets/daily-prices", { stock_id: "5236" })).rows as Row[];
  const sources = [...new Set(prices.map((r) => r.source))];
  expect(sources.sort()).toEqual(["tpex_otc_quotes", "twse_mi_index"]);
  await expect(page.getByTestId("source")).toBeVisible();
  for (const [label, source] of [["證交所", "twse_mi_index"], ["櫃買中心", "tpex_otc_quotes"]]) {
    await page.getByTestId("source").getByRole("radio", { name: label }).click();
    await page.waitForTimeout(200);
    const { axis, series } = await chartOption(page);
    const own = prices.filter((r) => r.source === source);
    compareCandles(axis, find(series, "candles"), own, OHLC);
    expect(axis[0]).toBe(own[0].trade_date);
    expect(axis.at(-1)).toBe(own.at(-1)!.trade_date);
  }
  await page.getByRole("radio", { name: "還原價" }).click();
  await expect(page.getByTestId("events")).toBeVisible();
});

test("the theme toggles, is remembered, and defaults to dark", async ({ page }) => {
  await page.addInitScript((key) => localStorage.setItem("stockdc.apiKey", key), KEY);
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByTestId("theme-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("toggling the theme repaints the chart in the new theme's colours", async ({ page }) => {
  // Code review of #69: the palette was read while rendering, before the
  // theme reached <html>, so the chart kept the old theme's colours.
  await withKey(page, "dark");
  await page.goto("/#/stock/2330");
  await chartOption(page);
  for (const theme of ["light", "dark", "light"]) {
    await page.getByTestId("theme-toggle").click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const [drawn, css] = await page.evaluate(() => {
      const series = window.__stockdc!.charts.price.getOption().series as { id: string; itemStyle: { color: string } }[];
      return [series.find((s) => s.id === "candles")!.itemStyle.color,
              getComputedStyle(document.documentElement).getPropertyValue("--up").trim()];
    });
    expect(drawn, theme).toBe(css);
  }
});

test("a stock without prices shows no endless loading in adjusted mode", async ({ page }) => {
  // Code review of #69: with no price source the adjusted request is never
  // sent, and the page waited for it forever. 1258 is delisted: listed, no data.
  await withKey(page);
  await page.addInitScript(() => localStorage.setItem("stockdc.mode", "adjusted"));
  await page.goto("/#/stock/1258");
  await expect(page.getByText("這檔股票沒有任何價格資料")).toBeVisible();
  await expect(page.getByText("載入中…")).toHaveCount(0);
});

test("a wrong key is refused and asked again", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("stockdc.apiKey", "wrong"));
  await page.goto("/#/stock/2330");
  await expect(page.getByText("這個 key 不被接受")).toBeVisible();
});

test("screenshots for the owner: both themes, desktop and phone", async ({ browser }) => {
  test.skip(!SHOTS, "set STOCKDC_SCREENSHOTS to a directory");
  for (const theme of ["dark", "light"] as const) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await withKey(page, theme);
    await page.goto("/#/stock/2330");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await chartOption(page);
    await page.screenshot({ path: `${SHOTS}/stock-2330-${theme}.png`, fullPage: true });
    await page.getByRole("radio", { name: "還原價" }).click();
    await expect(page.getByTestId("events")).toBeVisible();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SHOTS}/stock-2330-adjusted-${theme}.png`, fullPage: true });
    await page.goto("/");
    await page.getByTestId("home-search").fill("23");
    await page.screenshot({ path: `${SHOTS}/home-${theme}.png` });
    await page.close();

    const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
    await withKey(phone, theme);
    await phone.goto("/#/stock/6488");
    await chartOption(phone);
    const width = await phone.evaluate(() => document.documentElement.scrollWidth);
    expect(width, "no horizontal page scroll on a phone").toBeLessThanOrEqual(390);
    await phone.screenshot({ path: `${SHOTS}/phone-6488-${theme}.png`, fullPage: true });
    await phone.close();
  }
});
