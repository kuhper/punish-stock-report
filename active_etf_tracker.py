#!/usr/bin/env python3
"""
台股主動式 ETF 每日持股追蹤工具
=================================
功能：
  1. 從各投信官網抓取主動式 ETF 每日持股明細
  2. 儲存為 CSV 歷史紀錄
  3. 比對前一日持股，輸出「新增 / 刪除 / 權重變動」摘要
  4. 可選：輸出 HTML 報告

使用方式：
  pip install requests beautifulsoup4 playwright
  playwright install chromium
  python active_etf_tracker.py              # 抓全部 + 顯示變動
  python active_etf_tracker.py --etf 00981A # 只抓指定 ETF
  python active_etf_tracker.py --html       # 輸出 HTML 報告
"""

import requests, json, html as html_mod, re, csv, os, sys, argparse
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup

# Windows console UTF-8 fix — 避免 ✓ ✗ 印中文時崩潰
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

DATA_DIR = Path(__file__).parent / "etf_data"
DATA_DIR.mkdir(exist_ok=True)

ACTIVE_ETFS = {
    "00981A": {"name": "主動統一台股增長", "issuer": "統一", "method": "ezmoney", "fund_code": "49YTW"},
    "00403A": {"name": "主動統一升級50",   "issuer": "統一", "method": "ezmoney", "fund_code": "63YTW"},
    "00988A": {"name": "主動統一全球創新", "issuer": "統一", "method": "ezmoney", "fund_code": "61YTW"},
    "00982A": {"name": "主動群益台灣強棒", "issuer": "群益", "method": "capital",
               "url": "https://www.capitalfund.com.tw/etf/product/detail/399/buyback"},
    "00980A": {"name": "主動野村臺灣優選", "issuer": "野村", "method": "nomura", "fund_no": "00980A"},
    "00985A": {"name": "主動野村台灣50",   "issuer": "野村", "method": "nomura", "fund_no": "00985A"},
    "00999A": {"name": "主動野村策略高息", "issuer": "野村", "method": "nomura", "fund_no": "00999A"},
    "00984A": {"name": "主動安聯台灣高息", "issuer": "安聯", "method": "allianz",
               "url": "https://etf.allianzgi.com.tw/etf-info/E0001?tab=4"},
    "00991A": {"name": "主動復華未來50",   "issuer": "復華", "method": "generic_pw",
               "url": "https://www.fhtrust.com.tw/ETF/Fund/Product?fundid=5928&tabid=holding"},
    "00987A": {"name": "主動台新優勢成長", "issuer": "台新", "method": "generic_pw",
               "url": "https://www.tsit.com.tw/ETF/Fund/Holding?id=00987A"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}

# ── Telegram 設定（從環境變數讀，或直接填）─────────────────────────────────
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID", "")

# ── GitHub Pages 部署 ──────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = "kuhper/punish-stock-report"


def deploy_etf_to_github(html_path):
    """將 ETF 報告部署到 GitHub Pages 的 etf.html"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("  GitHub 部署跳過：未設定 GITHUB_TOKEN 或 GITHUB_REPO")
        return False
    import base64
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/etf.html"
    headers_gh = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    content = Path(html_path).read_text(encoding="utf-8")
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    sha = None
    try:
        r = requests.get(api_url, headers=headers_gh, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass
    payload = {
        "message": f"更新 ETF 持股報告 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(api_url, headers=headers_gh, json=payload, timeout=30)
        r.raise_for_status()
        print(f"  GitHub Pages etf.html 部署成功")
        return True
    except Exception as e:
        print(f"  GitHub Pages 部署失敗: {e}")
        return False


def _tg_summary(all_results) -> str:
    """產生 Telegram 推送的精簡文字摘要（Markdown）"""
    today = datetime.now().strftime("%Y-%m-%d")
    total_etfs = len([r for r in all_results if r[2]])
    total_added = sum(len(r[3]["added"]) for r in all_results if r[3])
    total_removed = sum(len(r[3]["removed"]) for r in all_results if r[3])
    total_changed = sum(len(r[3]["weight_changed"]) for r in all_results if r[3])

    # 跨 ETF 共識：top 5 加碼 / top 5 減碼 / top 5 新增
    cross_buy, cross_sell, cross_new = {}, {}, {}
    for etf_code, cfg, holdings, diff in all_results:
        if not diff: continue
        for w in diff["weight_changed"]:
            target = cross_buy if w["delta"] > 0 else cross_sell
            t = target.setdefault(w["code"], {"name": w["name"], "etfs": [], "total_delta": 0})
            t["etfs"].append(etf_code)
            t["total_delta"] += w["delta"]
        for a in diff["added"]:
            t = cross_new.setdefault(a["code"], {"name": a["name"], "etfs": []})
            t["etfs"].append(etf_code)

    lines = [
        f"🎯 *主動式 ETF 持股動態*  `{today}`",
        f"━━━━━━━━━━━━━━━━━━",
        f"📊 追蹤 {total_etfs} 檔 ｜ ➕{total_added} 新增 ｜ ➖{total_removed} 移除 ｜ 🔄{total_changed} 異動",
    ]

    if cross_buy:
        top = sorted(cross_buy.items(), key=lambda x: x[1]["total_delta"], reverse=True)[:5]
        lines.append("\n🔴 *共同加碼 Top 5*")
        for code, info in top:
            n = len(info["etfs"])
            d = info["total_delta"]
            lines.append(f"  `{code}` {info['name']}  {n}家  *{d:+.2f}%*")

    if cross_sell:
        top = sorted(cross_sell.items(), key=lambda x: x[1]["total_delta"])[:5]
        lines.append("\n🟢 *共同減碼 Top 5*")
        for code, info in top:
            n = len(info["etfs"])
            d = info["total_delta"]
            lines.append(f"  `{code}` {info['name']}  {n}家  *{d:+.2f}%*")

    if cross_new:
        top = sorted(cross_new.items(), key=lambda x: len(x[1]["etfs"]), reverse=True)[:5]
        lines.append("\n✨ *多家新增 Top 5*")
        for code, info in top:
            n = len(info["etfs"])
            lines.append(f"  `{code}` {info['name']}  {n}家")

    return "\n".join(lines)


def send_telegram(summary: str, html_path: Path | None = None):
    """送 Telegram：先傳文字摘要，再傳 HTML 報告附件"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("  (略過 Telegram：未設定 TOKEN/CHAT_ID)")
        return
    api = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"

    # 1. 文字摘要 (Markdown)
    try:
        r = requests.post(
            f"{api}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": summary, "parse_mode": "Markdown"},
            timeout=15,
        )
        r.raise_for_status()
        print(f"  ✓ Telegram 文字摘要已送出")
    except Exception as e:
        print(f"  ✗ Telegram 文字傳送失敗: {e}")

    # 2. HTML 報告附件
    if html_path and html_path.exists():
        try:
            with open(html_path, "rb") as f:
                r = requests.post(
                    f"{api}/sendDocument",
                    data={"chat_id": TG_CHAT_ID,
                          "caption": f"主動式 ETF 詳細報告 {datetime.now().strftime('%Y-%m-%d')}\n💡 iPhone 請點 ⋯ → 在 Safari 開啟"},
                    files={"document": (html_path.name, f, "text/html")},
                    timeout=30,
                )
            r.raise_for_status()
            print(f"  ✓ Telegram HTML 附件已送出")
        except Exception as e:
            print(f"  ✗ Telegram 附件傳送失敗: {e}")


# ---- Fetchers ----

def fetch_ezmoney(etf_code, cfg):
    fund_code = cfg["fund_code"]
    url = f"https://www.ezmoney.com.tw/ETF/Fund/Info?FundCode={fund_code}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.find(id="DataAsset")
    if not el:
        raise ValueError(f"找不到 DataAsset ({etf_code})")
    decoded = html_mod.unescape(el.get("data-content", ""))
    assets = json.loads(decoded)
    holdings = []
    for a in assets:
        if a.get("AssetCode") == "ST" and a.get("Details"):
            for d in a["Details"]:
                holdings.append({
                    "etf": etf_code,
                    "date": d.get("TranDate", "")[:10],
                    "code": d.get("DetailCode", ""),
                    "name": d.get("DetailName", ""),
                    "shares": d.get("Share", 0),
                    "weight": d.get("NavRate", 0),
                })
    return sorted(holdings, key=lambda x: float(x["weight"] or 0), reverse=True)


def _get_browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    return pw, browser


def _click_show_more(page):
    """嘗試多種方式點擊「查看更多/顯示更多/展開全部」（只點一次展開，不反覆 toggle）"""
    expanded = page.evaluate("""() => {
        const expand_targets = ['查看更多', '顯示更多', '展開全部'];
        // 先試群益專用按鈕（只在文字是「展開」類才點）
        const toggle = document.querySelector('.pct-stock-table-tbody-toggle-btn');
        if (toggle && toggle.offsetParent !== null && expand_targets.includes(toggle.textContent.trim())) {
            toggle.click(); return true;
        }
        // 再試通用選擇器
        const ps = document.querySelectorAll('td.showMore p, a, button, span, p, div');
        for (const el of ps) {
            const t = el.textContent.trim();
            if (expand_targets.includes(t) && el.offsetParent !== null && el.children.length <= 1) {
                el.click(); return true;
            }
        }
        return false;
    }""")
    if expanded:
        page.wait_for_timeout(3000)


def fetch_capital(etf_code, cfg, browser):
    page = browser.new_page()
    try:
        page.goto(cfg["url"], timeout=30000)
        page.wait_for_timeout(5000)
        try:
            page.wait_for_selector(".pct-stock-table-tbody", timeout=8000)
            _click_show_more(page)
            page.wait_for_timeout(2000)
        except:
            pass
        rows = page.evaluate("""() => {
            const trs = document.querySelectorAll('.pct-stock-table-tbody .tr');
            return Array.from(trs).map(tr => {
                const tds = tr.querySelectorAll('.td, .th');
                return Array.from(tds).map(c => c.textContent.trim());
            }).filter(r => r.length >= 3);
        }""")
        today = datetime.now().strftime("%Y-%m-%d")
        holdings = []
        for row in rows:
            if len(row) >= 4 and re.match(r"\d{4}", row[0]):
                holdings.append({
                    "etf": etf_code, "date": today,
                    "code": row[0], "name": row[1],
                    "weight": float(row[2].replace("%", "")),
                    "shares": int(row[3].replace(",", "")) if row[3].replace(",", "").isdigit() else 0,
                })
        return sorted(holdings, key=lambda x: x["weight"], reverse=True)
    finally:
        page.close()


def _parse_stock_lines(text, etf_code):
    lines = text.split("\n")
    holdings = []
    in_stock = False
    today = datetime.now().strftime("%Y-%m-%d")
    for line in lines:
        line = line.strip()
        # 表頭可能在一行（安聯）或拆成多行（野村），只要看到「股票代號/代碼」就進入解析模式
        if ("股票代號" in line or "股票代碼" in line):
            in_stock = True
            continue
        if in_stock:
            if line == "期貨" or line.startswith("期貨代碼") or line.startswith("期貨代號"):
                break
            if not line or line in ("顯示更多", "查看更多", "收合", "展開全部"):
                continue
            # 跳過野村分行的子表頭（股票名稱、股數、權重(%)）
            if line in ("股票名稱", "股數", "序號") or "權重" in line:
                continue
            parts = line.split("\t")
            if len(parts) >= 4 and re.match(r"\d{4}", parts[0]):
                shares_str = parts[2].replace(",", "")
                weight_str = parts[3].replace("%", "")
                holdings.append({
                    "etf": etf_code, "date": today,
                    "code": parts[0], "name": parts[1],
                    "shares": int(shares_str) if shares_str.isdigit() else 0,
                    "weight": float(weight_str) if weight_str.replace(".", "").isdigit() else 0,
                })
            # 安聯格式: 序號\t代號\t名稱\t股數\t權重%
            elif len(parts) >= 5 and re.match(r"\d+$", parts[0]) and re.match(r"\d{4}", parts[1]):
                shares_str = parts[3].replace(",", "")
                weight_str = parts[4].replace("%", "")
                holdings.append({
                    "etf": etf_code, "date": today,
                    "code": parts[1], "name": parts[2],
                    "shares": int(shares_str) if shares_str.isdigit() else 0,
                    "weight": float(weight_str) if weight_str.replace(".", "").isdigit() else 0,
                })
    return sorted(holdings, key=lambda x: x["weight"], reverse=True)


def fetch_nomura(etf_code, cfg, browser):
    page = browser.new_page()
    try:
        fund_no = cfg["fund_no"]
        url = f"https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo={fund_no}&tab=Shareholding"
        page.goto(url, timeout=30000)
        page.wait_for_timeout(5000)
        _click_show_more(page)
        text = page.evaluate("() => document.body.innerText")
        return _parse_stock_lines(text, etf_code)
    finally:
        page.close()


def fetch_allianz(etf_code, cfg, browser):
    page = browser.new_page()
    try:
        page.goto(cfg["url"], timeout=30000)
        page.wait_for_timeout(5000)
        _click_show_more(page)
        text = page.evaluate("() => document.body.innerText")
        return _parse_stock_lines(text, etf_code)
    finally:
        page.close()


def fetch_generic_pw(etf_code, cfg, browser):
    page = browser.new_page()
    try:
        page.goto(cfg["url"], timeout=30000)
        page.wait_for_timeout(5000)
        _click_show_more(page)
        text = page.evaluate("() => document.body.innerText")
        return _parse_stock_lines(text, etf_code)
    finally:
        page.close()


FETCHERS = {
    "ezmoney": fetch_ezmoney,
    "capital": fetch_capital,
    "nomura":  fetch_nomura,
    "allianz": fetch_allianz,
    "generic_pw": fetch_generic_pw,
}


# ---- History & Diff ----

def save_holdings(etf_code, holdings):
    csv_path = DATA_DIR / f"{etf_code}.csv"
    file_exists = csv_path.exists()
    fieldnames = ["etf", "date", "code", "name", "shares", "weight"]
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for h in holdings:
            writer.writerow(h)


def load_previous(etf_code, today_date):
    csv_path = DATA_DIR / f"{etf_code}.csv"
    if not csv_path.exists():
        return []
    all_rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            all_rows.append(row)
    dates = sorted(set(r["date"] for r in all_rows if r["date"] != today_date), reverse=True)
    if not dates:
        return []
    return [r for r in all_rows if r["date"] == dates[0]]


def compute_diff(prev, curr):
    prev_map = {r["code"]: r for r in prev}
    curr_map = {r["code"]: r for r in curr}
    added = [curr_map[c] for c in curr_map if c not in prev_map]
    removed = [prev_map[c] for c in prev_map if c not in curr_map]
    weight_changed = []
    for code in curr_map:
        if code in prev_map:
            w_prev = float(prev_map[code]["weight"] or 0)
            w_curr = float(curr_map[code]["weight"] or 0)
            delta = w_curr - w_prev
            if abs(delta) >= 0.01:
                weight_changed.append({**curr_map[code], "prev_weight": w_prev, "delta": round(delta, 2)})
    weight_changed.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return {"added": added, "removed": removed, "weight_changed": weight_changed}


# ---- Output ----

def print_report(etf_code, cfg, holdings, diff):
    print(f"\n{'='*60}")
    print(f"  {etf_code} {cfg['name']}  ({cfg['issuer']}投信)")
    if holdings:
        print(f"  資料日期: {holdings[0]['date']}  |  持股檔數: {len(holdings)}")
    print(f"{'='*60}")
    print(f"\n  {'代碼':>6} {'名稱':<10} {'權重%':>7} {'股數':>14}")
    print(f"  {'-'*42}")
    for h in holdings[:10]:
        w = float(h['weight'])
        s = int(float(h.get('shares', 0)))
        print(f"  {h['code']:>6} {h['name']:<10} {w:>7.2f} {s:>14,}")
    if diff is None:
        print("\n  (首次抓取，無前日資料可比對)")
        return
    if diff["added"]:
        print(f"\n  + 新增 ({len(diff['added'])} 檔):")
        for a in diff["added"]:
            print(f"     + {a['code']} {a['name']}  權重 {float(a['weight']):.2f}%")
    if diff["removed"]:
        print(f"\n  - 刪除 ({len(diff['removed'])} 檔):")
        for r in diff["removed"]:
            print(f"     - {r['code']} {r['name']}  (前日 {float(r['weight']):.2f}%)")
    if diff["weight_changed"]:
        up = [w for w in diff["weight_changed"] if w["delta"] > 0]
        down = [w for w in diff["weight_changed"] if w["delta"] < 0]
        if up:
            print(f"\n  ^ 加碼 (前5):")
            for w in up[:5]:
                print(f"     {w['code']} {w['name']}  {w['prev_weight']:.2f}% -> {float(w['weight']):.2f}%  (+{w['delta']:.2f})")
        if down:
            print(f"\n  v 減碼 (前5):")
            for w in down[:5]:
                print(f"     {w['code']} {w['name']}  {w['prev_weight']:.2f}% -> {float(w['weight']):.2f}%  ({w['delta']:.2f})")
    if not diff["added"] and not diff["removed"] and not diff["weight_changed"]:
        print("\n  = 持股無變動")


def _load_spark_history(etf_code, stock_code, days=10):
    # Read this ETF's CSV and return up to last N weight values for stock_code.
    csv_path = DATA_DIR / f"{etf_code}.csv"
    if not csv_path.exists():
        return []
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("code") == stock_code:
                try:
                    rows.append((r["date"], float(r.get("weight") or 0)))
                except Exception:
                    pass
    rows.sort(key=lambda x: x[0])
    seen, spark = set(), []
    for d, w in rows:
        if d in seen:
            continue
        seen.add(d)
        spark.append(w)
    return spark[-days:]


def _spark_svg(data, w=80, h=22, color=None):
    if not data or len(data) < 2:
        return f'<svg width="{w}" height="{h}" style="display:block"></svg>'
    mn, mx = min(data), max(data)
    rng = mx - mn or 1
    step = w / (len(data) - 1)
    pts = " ".join(f"{i*step:.1f},{h - ((v-mn)/rng)*h:.1f}" for i, v in enumerate(data))
    first, last = data[0], data[-1]
    if not color:
        color = "var(--buy)" if last >= first else "var(--sell)"
    cx = (len(data) - 1) * step
    cy = h - ((last - mn) / rng) * h
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block">'
            f'<polyline fill="none" stroke="{color}" stroke-width="1.5" '
            f'stroke-linejoin="round" stroke-linecap="round" points="{pts}"/>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="1.8" fill="{color}"/></svg>')


_ACTION_CLS = {"加碼": "buy", "減碼": "sell", "新增": "new", "移除": "remove"}


_OUTSTANDING_CACHE = {}


def _load_outstanding_shares():
    # Fetch TWSE 已發行普通股數 once daily; cache to data/outstanding_shares.json
    global _OUTSTANDING_CACHE
    if _OUTSTANDING_CACHE:
        return _OUTSTANDING_CACHE
    import urllib.request
    cache = DATA_DIR / "outstanding_shares.json"
    today = datetime.now().strftime("%Y-%m-%d")
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("_date") == today:
                _OUTSTANDING_CACHE = d["data"]
                return _OUTSTANDING_CACHE
        except Exception:
            pass
    try:
        req = urllib.request.Request(
            "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read().decode("utf-8"))
        if not rows:
            return {}
        keys = list(rows[0].keys())
        code_key = keys[1]
        shares_key = None
        for k in keys:
            if "已發行普通股" in k or "已發行" in k or "發行股數" in k:
                shares_key = k
                break
        data = {}
        for row in rows:
            code = (row.get(code_key) or "").strip()
            s = (row.get(shares_key) or "0").strip() if shares_key else "0"
            try:
                data[code] = int(s)
            except Exception:
                data[code] = 0
        cache.write_text(json.dumps({"_date": today, "data": data}, ensure_ascii=False), encoding="utf-8")
        _OUTSTANDING_CACHE = data
        return data
    except Exception as e:
        print(f"  (outstanding shares fetch failed: {e})")
        if cache.exists():
            try:
                _OUTSTANDING_CACHE = json.loads(cache.read_text(encoding="utf-8")).get("data", {})
                return _OUTSTANDING_CACHE
            except Exception:
                pass
        return {}


def _compute_stock_history(stock_code, days=10):
    # Returns list of (date, shares_delta, weight_delta) across ALL ETFs, last N days.
    etf_series = {}
    for etf_csv in DATA_DIR.glob("*.csv"):
        etf_code = etf_csv.stem
        if etf_code in ("outstanding_shares",): continue
        d = {}
        try:
            with open(etf_csv, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r.get("code") == stock_code:
                        try:
                            d[r["date"]] = (
                                int(float(r.get("shares") or 0)),
                                float(r.get("weight") or 0),
                            )
                        except Exception:
                            pass
        except Exception:
            continue
        if d:
            etf_series[etf_code] = d
    if not etf_series:
        return []
    all_dates = sorted({date for s in etf_series.values() for date in s})
    cur_s = {e: 0 for e in etf_series}
    cur_w = {e: 0.0 for e in etf_series}
    agg = []
    for d in all_dates:
        for e, series in etf_series.items():
            if d in series:
                cur_s[e], cur_w[e] = series[d]
        agg.append((d, sum(cur_s.values()), sum(cur_w.values())))
    deltas = []
    for i in range(1, len(agg)):
        d   = agg[i][0]
        ds  = agg[i][1] - agg[i-1][1]
        dw  = agg[i][2] - agg[i-1][2]
        deltas.append((d, ds, dw))
    return deltas[-days:]


def _fmt_shares(n):
    if n == 0:
        return "—"
    sign = "+" if n > 0 else "−"
    n = abs(n)
    if n >= 1_000_000:
        return f"{sign}{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{sign}{n/1_000:.1f}K"
    return f"{sign}{n}"


def _fmt_capital_pct(net_shares, outstanding):
    if not outstanding or outstanding == 0:
        return "—"
    pct = net_shares / outstanding * 100
    if abs(pct) < 0.0001:
        return f"{'+' if pct >= 0 else '−'}{abs(pct)*1_000_000:.1f} ppm"
    sign = "+" if pct > 0 else "−" if pct < 0 else ""
    return f"{sign}{abs(pct):.4f}%"


# ─── HTML chunks (no f-string here; standard .format() with explicit placeholders) ───
_HEAD = (
    '<!doctype html><html lang="zh-Hant"><head>'
    '<meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<title>主動式 ETF 持股動態 {today}</title>'
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
    '&family=Noto+Sans+TC:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">'
)

# CSS uses doubled braces because it's passed to .format() below
_STYLE = """<style>
:root {{
  --bg:#0a1020;--bg-2:#0f1729;--surface:#131c33;--surface-2:#1a2540;
  --border:#1e2a47;--border-2:#2a3b62;
  --text:#e8edf7;--text-2:#a8b3cf;--text-3:#6b7898;
  --up:#ef4e5b;--up-bg:rgba(239,78,91,0.12);
  --down:#2dd4a4;--down-bg:rgba(45,212,164,0.12);
  --buy:#ff9347;--buy-bg:rgba(255,147,71,0.14);
  --sell:#5aa9ff;--sell-bg:rgba(90,169,255,0.14);
  --new:#b48cff;--new-bg:rgba(180,140,255,0.14);
  --remove:#6b7898;--remove-bg:rgba(107,120,152,0.14);
  --accent:#ef4e5b;
}}
*{{box-sizing:border-box}}
html,body{{margin:0;background:var(--bg);color:var(--text);
  font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;
  font-feature-settings:"tnum" 1;line-height:1.5;font-size:14px}}
.num{{font-family:"Inter",system-ui,sans-serif;font-feature-settings:"tnum" 1}}
.mono{{font-family:"JetBrains Mono","SF Mono",ui-monospace,monospace;font-feature-settings:"tnum" 1}}
#tg-banner{{display:none;background:linear-gradient(90deg,#0088cc,#00a8e8);
  color:#fff;padding:12px 16px;border-radius:10px;margin-bottom:16px;font-size:13px}}
.page{{max-width:1500px;margin:0 auto;padding:28px 32px}}
.page-header{{display:flex;align-items:flex-start;justify-content:space-between;
  margin-bottom:24px;gap:16px;flex-wrap:wrap}}
.brand-tag{{display:inline-flex;align-items:center;gap:10px;margin-bottom:6px;
  font-size:11px;letter-spacing:0.2em;color:var(--text-3);text-transform:uppercase}}
.brand-tag::before{{content:"";width:8px;height:8px;border-radius:2px;background:var(--accent)}}
h1{{font-size:28px;font-weight:600;margin:0;letter-spacing:-0.01em}}
.page-meta{{font-size:13px;color:var(--text-3);margin-top:6px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}}
.kpi-card{{padding:14px 18px;background:var(--surface);border:1px solid var(--border);
  border-radius:12px;min-width:0}}
.kpi-label{{font-size:11px;color:var(--text-3);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px}}
.kpi-value{{display:flex;align-items:baseline;gap:6px}}
.kpi-num{{font-size:32px;font-weight:600;color:var(--text);line-height:1;font-family:"Inter",system-ui,sans-serif}}
.kpi-num.new{{color:var(--new)}}
.kpi-num.remove{{color:var(--remove)}}
.kpi-unit{{font-size:13px;color:var(--text-3)}}
.kpi-sub{{font-size:12px;color:var(--text-2);margin-top:8px}}
.consensus{{background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:hidden;margin-bottom:28px}}
.consensus-header{{display:flex;align-items:center;justify-content:space-between;
  padding:16px 20px;border-bottom:1px solid var(--border);flex-wrap:wrap;gap:12px}}
.consensus-title{{font-size:15px;font-weight:600}}
.consensus-sub{{font-size:12px;color:var(--text-3);margin-top:2px}}
.tabs{{display:flex;gap:4px;padding:3px;background:var(--bg-2);border-radius:8px;border:1px solid var(--border)}}
.tab-btn{{padding:6px 12px;border-radius:6px;border:none;background:transparent;
  color:var(--text-3);font-size:12px;font-weight:500;cursor:pointer;font-family:inherit}}
.tab-btn.active{{background:var(--surface-2);color:var(--text)}}
.tab-btn .num{{opacity:0.6;margin-left:4px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
.consensus table thead tr{{background:var(--bg-2)}}
th{{text-align:left;padding:10px 16px;font-size:11px;font-weight:500;color:var(--text-3);
  letter-spacing:0.08em;text-transform:uppercase;border-bottom:1px solid var(--border)}}
th.right{{text-align:right}}
th.center{{text-align:center}}
td{{padding:11px 16px;font-size:13px;color:var(--text);border-top:1px solid var(--border)}}
td.right{{text-align:right}}
td.center{{text-align:center}}
td.dim2{{color:var(--text-2)}}
td.bold{{font-weight:500}}
.dim{{color:var(--text-3)}}
.delta.up{{color:var(--up)}}
.delta.down{{color:var(--down)}}
.delta.flat{{color:var(--text-3)}}
.dot-row{{display:inline-flex;align-items:center;gap:2px}}
.dot{{display:inline-block;width:6px;height:6px;border-radius:999px}}
.dot-row .count{{margin-left:6px;color:var(--text-3);font-size:12px}}
.chip-row{{display:flex;gap:6px;flex-wrap:wrap}}
.etf-chip{{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border-radius:6px;
  background:var(--bg-2);border:1px solid var(--border);font-size:11px}}
.etf-chip .mono{{color:var(--text-2)}}
.metric-cell{{padding:10px 14px;min-width:130px}}
.metric-value{{font-size:14px;font-weight:600;line-height:1.2}}
.metric-sub{{font-size:11px;color:var(--text-3);margin-top:2px;line-height:1.2}}
.metric-spark{{margin-top:4px;display:block}}
.hdr-sub{{display:block;font-size:9px;font-weight:400;letter-spacing:0;
  text-transform:none;color:var(--text-3);margin-top:2px;opacity:0.7}}
.etf-section{{background:var(--bg-2);border:1px solid var(--border);border-radius:14px;
  padding:22px 24px;margin-bottom:20px}}
.etf-header{{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:18px;gap:16px;flex-wrap:wrap}}
.etf-id{{display:flex;align-items:center;gap:18px;min-width:0}}
.etf-code{{padding:6px 12px;border:1px solid var(--border-2);border-radius:8px;
  font-size:14px;color:var(--text-2);letter-spacing:0.05em;flex-shrink:0}}
.etf-section h2{{font-size:20px;font-weight:600;margin:0;letter-spacing:-0.01em}}
.meta{{font-size:12px;color:var(--text-3);margin-top:4px}}
.etf-stats{{display:flex;gap:8px;flex-wrap:wrap}}
.stat-badge{{display:inline-flex;align-items:center;gap:6px;padding:6px 11px;
  background:var(--surface);border:1px solid var(--border);border-radius:8px;
  font-size:12px;font-weight:600}}
.filter-row{{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}}
.filter-btn{{padding:7px 14px;border-radius:8px;font-size:13px;cursor:pointer;
  border:1px solid var(--border);background:var(--surface);color:var(--text-2);
  font-family:inherit;transition:all 0.15s}}
.filter-btn:hover{{border-color:var(--border-2);color:var(--text)}}
.filter-btn .num{{margin-left:6px;opacity:0.6;font-weight:500}}
.filter-btn.active{{background:var(--surface-2);color:var(--text);
  border-color:var(--border-2);font-weight:600}}
.filter-btn.fb-buy.active   {{background:var(--buy-bg);color:var(--buy);border-color:var(--buy)}}
.filter-btn.fb-sell.active  {{background:var(--sell-bg);color:var(--sell);border-color:var(--sell)}}
.filter-btn.fb-new.active   {{background:var(--new-bg);color:var(--new);border-color:var(--new)}}
.filter-btn.fb-remove.active{{background:var(--remove-bg);color:var(--remove);border-color:var(--remove)}}
.table-wrap{{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;overflow-x:auto}}
.holdings-table thead th{{background:var(--surface);padding:10px 12px}}
.holdings-table td{{padding:11px 12px}}
.holdings-table tbody tr.removed td{{opacity:0.6}}
.bar-wrap{{position:relative;height:6px;background:var(--bg-2);border-radius:3px;
  margin-left:auto;width:90%;max-width:100px}}
.bar-prev{{position:absolute;left:0;top:0;bottom:0;background:var(--surface-2);border-radius:3px}}
.bar-curr{{position:absolute;left:0;top:0;bottom:0;border-right:1.5px solid var(--text-3)}}
.chip{{display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;
  font-size:11px;font-weight:600;letter-spacing:0.02em}}
.chip.buy{{color:var(--buy);background:var(--buy-bg)}}
.chip.sell{{color:var(--sell);background:var(--sell-bg)}}
.chip.new{{color:var(--new);background:var(--new-bg)}}
.chip.remove{{color:var(--remove);background:var(--remove-bg)}}
.footer{{margin-top:36px;padding-top:16px;border-top:1px solid var(--border);
  color:var(--text-3);font-size:12px;text-align:center}}
@media (max-width:768px){{
  .page{{padding:12px}}
  h1{{font-size:22px}}
  .kpis{{grid-template-columns:repeat(2,1fr);gap:8px}}
  .kpi-card{{padding:12px 14px}}
  .kpi-num{{font-size:22px}}
  .consensus-header{{padding:14px}}
  .etf-section{{padding:14px}}
  .etf-id{{gap:10px}}
  .etf-code{{padding:4px 8px;font-size:12px}}
  .etf-section h2{{font-size:16px}}
  .filter-row{{overflow-x:auto;flex-wrap:nowrap;-webkit-overflow-scrolling:touch}}
  .filter-btn{{flex:0 0 auto;padding:6px 12px;font-size:12px}}
  th,td{{padding:8px 10px;font-size:12px}}
  .holdings-table th,.holdings-table td{{padding:8px 8px}}
}}
</style></head><body>"""

# Body: needs .format with placeholders
_BODY = """<div class="page">
<div id="tg-banner">📱 <b>iPhone 使用者：</b>篩選按鈕在 Telegram 內可能無法使用。請點右上分享 → <b>「在 Safari 中開啟」</b>。</div>

<header class="page-header">
  <div>
    <div class="brand-tag">Active ETF Holdings Monitor</div>
    <h1>🎯 主動式 ETF 持股動態</h1>
    <div class="page-meta">資料日: <span class="num">{today}</span> · 每日 21:00 自動更新</div>
  </div>
</header>

<section class="kpis">
  <div class="kpi-card">
    <div class="kpi-label">追蹤 ETF</div>
    <div class="kpi-value"><span class="kpi-num">{total_etfs}</span><span class="kpi-unit">檔</span></div>
    <div class="kpi-sub">本期已更新</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">新增持股</div>
    <div class="kpi-value"><span class="kpi-num new">{total_added}</span><span class="kpi-unit">檔</span></div>
    <div class="kpi-sub">首度納入投組</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">移除持股</div>
    <div class="kpi-value"><span class="kpi-num remove">{total_removed}</span><span class="kpi-unit">檔</span></div>
    <div class="kpi-sub">本期已出清</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">權重異動</div>
    <div class="kpi-value"><span class="kpi-num">{total_changed}</span><span class="kpi-unit">筆</span></div>
    <div class="kpi-sub">加碼 <span style="color:var(--buy)">{up_count}</span> · 減碼 <span style="color:var(--sell)">{down_count}</span></div>
  </div>
</section>

<section class="consensus">
  <div class="consensus-header">
    <div>
      <div class="consensus-title">跨 ETF 共識</div>
      <div class="consensus-sub">多家同時動作的標的，依合計權重變動排序</div>
    </div>
    <div class="tabs">
      <button class="tab-btn active" data-tab="up">共同加碼<span class="num">{n_up}</span></button>
      <button class="tab-btn" data-tab="down">共同減碼<span class="num">{n_down}</span></button>
      <button class="tab-btn" data-tab="new">共同新增<span class="num">{n_new}</span></button>
    </div>
  </div>
  <table>
    <thead><tr>
      <th>代號</th><th>名稱</th><th class="center">家數</th>
      <th>合計變動<span class="hdr-sub">10 日走勢</span></th>
      <th>淨買股數<span class="hdr-sub">股本占比 · 10 日走勢</span></th>
      <th>各 ETF 明細</th>
    </tr></thead>
    <tbody data-tab-content="up">{consensus_up}</tbody>
    <tbody data-tab-content="down" hidden>{consensus_down}</tbody>
    <tbody data-tab-content="new" hidden>{consensus_new}</tbody>
  </table>
</section>

{etf_sections}

<p class="footer">資料來源：各投信官網每日持股揭露 · 僅供參考，不構成投資建議</p>
</div>"""

_SCRIPT = """<script>
(function(){
  var ua=navigator.userAgent||"";
  if(/Telegram/i.test(ua)||window.TelegramWebviewProxy!==undefined){
    var b=document.getElementById("tg-banner"); if(b) b.style.display="block";
  }
})();
document.querySelectorAll(".tab-btn").forEach(function(b){
  b.addEventListener("click",function(){
    var sec=b.closest("section");
    sec.querySelectorAll(".tab-btn").forEach(function(x){x.classList.toggle("active",x===b);});
    var tgt=b.getAttribute("data-tab");
    sec.querySelectorAll("[data-tab-content]").forEach(function(t){
      t.hidden=t.getAttribute("data-tab-content")!==tgt;
    });
  });
});
document.querySelectorAll(".filter-btn").forEach(function(b){
  b.addEventListener("click",function(){
    var id=b.getAttribute("data-filter-target");
    var f=b.getAttribute("data-filter");
    var tbl=document.getElementById(id); if(!tbl) return;
    b.parentElement.querySelectorAll('[data-filter-target="'+id+'"]').forEach(function(x){
      x.classList.toggle("active",x===b);
    });
    tbl.querySelectorAll("tbody tr").forEach(function(tr){
      tr.hidden=f!=="all"&&tr.getAttribute("data-status")!==f;
    });
  });
});
</script></body></html>"""


def generate_html_report(all_results):
    today    = datetime.now().strftime("%Y-%m-%d")
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    total_etfs    = len([r for r in all_results if r[2]])
    total_added   = sum(len(r[3]["added"]) for r in all_results if r[3])
    total_removed = sum(len(r[3]["removed"]) for r in all_results if r[3])
    total_changed = sum(len(r[3]["weight_changed"]) for r in all_results if r[3])
    up_count   = sum(1 for r in all_results if r[3] for w in r[3]["weight_changed"] if w["delta"] > 0)
    down_count = sum(1 for r in all_results if r[3] for w in r[3]["weight_changed"] if w["delta"] < 0)

    cross_buy, cross_sell, cross_new = {}, {}, {}
    for etf_code, cfg, holdings, diff in all_results:
        if not diff:
            continue
        for w in diff["weight_changed"]:
            target = cross_buy if w["delta"] > 0 else cross_sell
            t = target.setdefault(w["code"], {"name": w["name"], "details": [], "total": 0})
            t["details"].append({"etf": etf_code, "delta": w["delta"]})
            t["total"] += w["delta"]
        for a in diff["added"]:
            t = cross_new.setdefault(a["code"], {"name": a["name"], "etfs": []})
            t["etfs"].append(etf_code)

    outstanding = _load_outstanding_shares()

    def _consensus_rows(data, mode):
        if mode == "up":
            items = sorted(data.items(), key=lambda x: x[1]["total"], reverse=True)
        elif mode == "down":
            items = sorted(data.items(), key=lambda x: x[1]["total"])
        else:
            items = sorted(data.items(), key=lambda x: len(x[1].get("etfs", [])), reverse=True)
        dot_color = {"up": "var(--buy)", "down": "var(--sell)", "new": "var(--new)"}[mode]
        rows = []
        for code, info in items:
            details = info.get("details") or [{"etf": e, "delta": None} for e in info.get("etfs", [])]
            count = len(details)
            dots = "".join(f'<span class="dot" style="background:{dot_color}"></span>' for _ in range(count))

            # ── 合計變動 (weight delta) ────────────────────────────────────
            if mode == "new":
                total_html  = '<span class="chip new" style="font-size:11px">新增</span>'
                weight_cls  = "new"
            else:
                total = info["total"]
                weight_cls = "up" if total > 0 else "down" if total < 0 else "flat"
                sign = "+" if total > 0 else ""
                total_html = f'<span class="num delta {weight_cls}">{sign}{total:.2f}%</span>'

            # ── 歷史 (淨買股數 + 合計變動 走勢) ────────────────────────────
            history = _compute_stock_history(code, days=10)
            shares_series = [d[1] for d in history]
            weight_series = [d[2] for d in history]
            today_shares  = shares_series[-1] if shares_series else 0
            outs          = outstanding.get(code, 0)

            spark_w_color = f"var(--{weight_cls})" if weight_cls in ("buy", "sell", "new") else (
                "var(--up)" if mode == "up" else "var(--down)" if mode == "down" else "var(--new)"
            )
            spark_s_color = "var(--up)" if today_shares > 0 else "var(--down)" if today_shares < 0 else "var(--text-3)"

            spark_w_svg = _spark_svg(weight_series, w=80, h=18, color=spark_w_color) if len(weight_series) >= 2 else '<span class="dim">—</span>'
            spark_s_svg = _spark_svg(shares_series, w=80, h=18, color=spark_s_color) if len(shares_series) >= 2 else '<span class="dim">—</span>'

            shares_str = _fmt_shares(today_shares)
            cap_pct    = _fmt_capital_pct(today_shares, outs)
            net_cls    = "up" if today_shares > 0 else "down" if today_shares < 0 else "flat"

            chips_html = ""
            for d in details:
                d_html = ""
                if d.get("delta") is not None:
                    dv = d["delta"]
                    dcls = "up" if dv > 0 else "down" if dv < 0 else "flat"
                    sgn = "+" if dv > 0 else ""
                    d_html = f'<span class="num delta {dcls}">{sgn}{dv:.2f}%</span>'
                chips_html += f'<span class="etf-chip"><span class="mono">{d["etf"]}</span>{d_html}</span>'

            rows.append(
                f'<tr>'
                f'<td class="mono dim">{code}</td>'
                f'<td>{info["name"]}</td>'
                f'<td class="center"><span class="dot-row">{dots}<span class="num count">{count}</span></span></td>'
                f'<td class="metric-cell">'
                f'  <div class="metric-value">{total_html}</div>'
                f'  <div class="metric-spark">{spark_w_svg}</div>'
                f'</td>'
                f'<td class="metric-cell">'
                f'  <div class="metric-value num delta {net_cls}">{shares_str}</div>'
                f'  <div class="metric-sub num">{cap_pct} 股本</div>'
                f'  <div class="metric-spark">{spark_s_svg}</div>'
                f'</td>'
                f'<td><div class="chip-row">{chips_html}</div></td>'
                f'</tr>'
            )
        return "\n".join(rows) or '<tr><td colspan="6" class="center dim" style="padding:20px">無資料</td></tr>'

    consensus_up   = _consensus_rows(cross_buy,  "up")
    consensus_down = _consensus_rows(cross_sell, "down")
    consensus_new  = _consensus_rows(cross_new,  "new")

    etf_sections = []
    for idx, (etf_code, cfg, holdings, diff) in enumerate(all_results):
        if not holdings:
            continue

        added_set   = {a["code"] for a in diff["added"]} if diff else set()
        removed_map = {r["code"]: r for r in diff["removed"]} if diff else {}
        change_map  = {w["code"]: w for w in diff["weight_changed"]} if diff else {}

        e_added   = len(diff["added"]) if diff else 0
        e_removed = len(diff["removed"]) if diff else 0
        e_up      = sum(1 for w in (diff["weight_changed"] if diff else []) if w["delta"] > 0)
        e_down    = sum(1 for w in (diff["weight_changed"] if diff else []) if w["delta"] < 0)

        holdings_sorted = sorted(holdings, key=lambda h: float(h.get("weight") or 0), reverse=True)
        data_date = holdings_sorted[0].get("date", today) if holdings_sorted else today

        rows_html = []
        count_by = {"加碼": 0, "減碼": 0, "新增": 0, "移除": 0, "持平": 0}
        for h in holdings_sorted:
            code, name = h["code"], h["name"]
            try:
                curr = float(h.get("weight") or 0)
            except Exception:
                curr = 0
            try:
                shares = int(float(h.get("shares") or 0))
            except Exception:
                shares = 0

            if code in added_set:
                action, prev, delta = "新增", 0, curr
            elif code in change_map:
                d = change_map[code]
                delta = d["delta"]
                prev = d.get("prev_weight", curr - delta)
                action = "加碼" if delta > 0 else "減碼"
            else:
                action, prev, delta = "持平", curr, 0
            count_by[action] = count_by.get(action, 0) + 1

            cls = _ACTION_CLS.get(action, "")
            delta_cls = "up" if delta > 0 else "down" if delta < 0 else "flat"
            delta_str = ("+" if delta > 0 else "") + f"{delta:.2f}"

            spark = _load_spark_history(etf_code, code, days=10)
            if not spark:
                spark = [prev, curr] if prev != curr else []
            spark_color = f"var(--{cls})" if cls else None
            spark_svg = _spark_svg(spark, w=90, h=22, color=spark_color) if spark else '<span class="dim">—</span>'

            curr_bar = min(curr * 8, 100)
            prev_bar = min(prev * 8, 100)
            prev_text = "—" if prev == 0 else f"{prev:.2f}%"

            rows_html.append(
                f'<tr data-status="{action}">'
                f'<td class="mono dim">{code}</td>'
                f'<td class="bold">{name}</td>'
                f'<td class="right num">{shares:,}</td>'
                f'<td class="right num dim2">{prev_text}</td>'
                f'<td class="right num bold">{curr:.2f}%</td>'
                f'<td class="right num delta {delta_cls}">{delta_str}</td>'
                f'<td class="center">{spark_svg}</td>'
                f'<td class="center"><span class="chip {cls}">{action}</span></td>'
                f'<td class="right">'
                f'<div class="bar-wrap">'
                f'<div class="bar-prev" style="width:{prev_bar}%"></div>'
                f'<div class="bar-curr" style="width:{curr_bar}%"></div>'
                f'</div></td></tr>'
            )

        for code, r in removed_map.items():
            try:
                rw = float(r.get("weight") or 0)
            except Exception:
                rw = 0
            count_by["移除"] = count_by.get("移除", 0) + 1
            rows_html.append(
                f'<tr data-status="移除" class="removed">'
                f'<td class="mono dim">{code}</td><td>{r["name"]}</td>'
                f'<td class="right num dim">—</td><td class="right num dim2">{rw:.2f}%</td>'
                f'<td class="right num dim">—</td>'
                f'<td class="right num delta down">−{rw:.2f}</td>'
                f'<td class="center dim">—</td>'
                f'<td class="center"><span class="chip remove">移除</span></td>'
                f'<td></td></tr>'
            )

        filter_btns = [f'<button class="filter-btn active" data-filter-target="tbl-{idx}" data-filter="all">全部</button>']
        for label, key in [("加碼", "加碼"), ("減碼", "減碼"), ("新增", "新增"), ("移除", "移除")]:
            n = count_by.get(key, 0)
            cls_b = _ACTION_CLS[key]
            filter_btns.append(
                f'<button class="filter-btn fb-{cls_b}" data-filter-target="tbl-{idx}" data-filter="{key}">{label}<span class="num">{n}</span></button>'
            )

        etf_sections.append(
            '<section class="etf-section">'
            '<div class="etf-header">'
            f'<div class="etf-id"><div class="etf-code mono">{etf_code}</div>'
            f'<div><h2>{cfg["name"]}</h2>'
            f'<div class="meta">{cfg["issuer"]}投信 · 資料截止 <span class="num">{data_date}</span> · 持股 <span class="num">{len(holdings)}</span> 檔</div>'
            '</div></div>'
            '<div class="etf-stats">'
            f'<div class="stat-badge"><span style="color:var(--new)">+{e_added}</span><span class="dim">新增</span></div>'
            f'<div class="stat-badge"><span style="color:var(--remove)">−{e_removed}</span><span class="dim">移除</span></div>'
            f'<div class="stat-badge"><span class="delta up">▲ {e_up}</span><span class="dim">加碼</span></div>'
            f'<div class="stat-badge"><span class="delta down">▼ {e_down}</span><span class="dim">減碼</span></div>'
            '</div></div>'
            f'<div class="filter-row">{"".join(filter_btns)}</div>'
            '<div class="table-wrap">'
            f'<table id="tbl-{idx}" class="holdings-table">'
            '<thead><tr>'
            '<th>代號</th><th>名稱</th><th class="right">股數</th>'
            '<th class="right">前期權重</th><th class="right">本期權重</th><th class="right">Δ</th>'
            '<th class="center">30 日趨勢</th><th class="center">異動</th><th class="right">占比視覺</th>'
            '</tr></thead><tbody>' + "".join(rows_html) + '</tbody></table></div></section>'
        )

    head = _HEAD.format(today=today)
    style = _STYLE.format()
    body = _BODY.format(
        gen_time=gen_time, today=today,
        total_etfs=total_etfs, total_added=total_added, total_removed=total_removed,
        total_changed=total_changed, up_count=up_count, down_count=down_count,
        n_up=len(cross_buy), n_down=len(cross_sell), n_new=len(cross_new),
        consensus_up=consensus_up, consensus_down=consensus_down, consensus_new=consensus_new,
        etf_sections="".join(etf_sections),
    )
    return head + style + body + _SCRIPT


# ---- Main ----

def main():
    parser = argparse.ArgumentParser(description="台股主動式 ETF 每日持股追蹤")
    parser.add_argument("--etf", nargs="*", help="指定 ETF 代碼，不指定則抓全部")
    parser.add_argument("--no-html", action="store_true", help="跳過 HTML 報告產生（預設都會產）")
    parser.add_argument("--no-telegram", action="store_true", help="跳過 Telegram 推播（預設都會推）")
    parser.add_argument("--list", action="store_true", help="列出支援的 ETF")
    parser.add_argument("--no-save", action="store_true", help="不儲存歷史紀錄")
    parser.add_argument("--deploy", action="store_true", help="部署到 GitHub Pages")
    args = parser.parse_args()

    if args.list:
        print(f"\n支援的主動式 ETF ({len(ACTIVE_ETFS)} 檔):\n")
        for code, cfg in sorted(ACTIVE_ETFS.items()):
            print(f"  {code}  {cfg['name']:<16s}  {cfg['issuer']}投信  [{cfg['method']}]")
        return

    targets = args.etf if args.etf else list(ACTIVE_ETFS.keys())
    targets = [t for t in targets if t in ACTIVE_ETFS]
    if not targets:
        print("錯誤：未指定有效的 ETF 代碼。用 --list 查看支援清單。")
        return

    need_pw = any(ACTIVE_ETFS[t]["method"] != "ezmoney" for t in targets)
    pw, browser = None, None
    if need_pw:
        try:
            pw, browser = _get_browser()
        except Exception as e:
            print(f"Playwright 啟動失敗: {e}")
            print("  只能抓取統一投信的 ETF。請先安裝：pip install playwright && playwright install chromium")
            targets = [t for t in targets if ACTIVE_ETFS[t]["method"] == "ezmoney"]

    all_results = []
    for etf_code in targets:
        cfg = ACTIVE_ETFS[etf_code]
        method = cfg["method"]
        fetcher = FETCHERS.get(method)
        if not fetcher:
            print(f"  {etf_code}: 不支援的方法 {method}")
            continue
        try:
            sys.stdout.write(f"  抓取 {etf_code} {cfg['name']}...")
            sys.stdout.flush()
            if method == "ezmoney":
                holdings = fetcher(etf_code, cfg)
            else:
                holdings = fetcher(etf_code, cfg, browser)
            print(f" {len(holdings)} 檔持股")
            today_str = holdings[0]["date"] if holdings else datetime.now().strftime("%Y-%m-%d")
            prev = load_previous(etf_code, today_str)
            diff = compute_diff(prev, holdings) if prev else None
            if not args.no_save and holdings:
                csv_path = DATA_DIR / f"{etf_code}.csv"
                already = False
                if csv_path.exists():
                    with open(csv_path, "r", encoding="utf-8-sig") as f:
                        for row in csv.DictReader(f):
                            if row.get("date") == today_str:
                                already = True
                                break
                if not already:
                    save_holdings(etf_code, holdings)
            print_report(etf_code, cfg, holdings, diff)
            all_results.append((etf_code, cfg, holdings, diff))
        except Exception as e:
            print(f" 錯誤: {e}")
            import traceback
            traceback.print_exc()

    if browser:
        browser.close()
    if pw:
        pw.stop()

    report_path = None
    if not args.no_html and all_results:
        html_content = generate_html_report(all_results)
        date_str = datetime.now().strftime("%Y%m%d")
        report_path = DATA_DIR / f"report_{date_str}.html"
        report_path.write_text(html_content, encoding="utf-8")
        print(f"\nHTML 報告已儲存: {report_path}")

    if not args.no_telegram and all_results:
        print("\n推送 Telegram …")
        summary = _tg_summary(all_results)
        send_telegram(summary, report_path)

    # GitHub Pages 部署
    if args.deploy and report_path and report_path.exists():
        deploy_etf_to_github(report_path)

    print(f"\n歷史資料目錄: {DATA_DIR}")



if __name__ == "__main__":
    main()
