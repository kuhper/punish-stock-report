#!/usr/bin/env python3
"""
台股處置股追蹤工具
==================
功能：
  1. 從 TWSE/TPEx 抓取目前處置中的股票清單
  2. 解析處置原因、處置起迄日（出關日）
  3. 查詢每檔股票的產業分類
  4. 分析「族群聚集」：哪些產業同時多檔被處置
  5. 分析「即將出關」：哪些產業即將集體解除處置
  6. 輸出 HTML 報告

使用方式：
  pip install requests
  python punish_tracker.py           # 抓取 + 產出報告
  python punish_tracker.py --days 5  # 只看 5 天內出關的
"""

import requests, json, re, sys, argparse, csv, os
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict, Counter

DATA_DIR = Path(__file__).parent / "punish_data"
DATA_DIR.mkdir(exist_ok=True)
SUB_INDUSTRY_COUNTS = {}  # 每個子產業的全族群股票數

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}

INDUSTRY_NAMES = {
    "01":"水泥","02":"食品","03":"塑膠","04":"紡織纖維","05":"電機機械",
    "06":"電器電纜","08":"玻璃陶瓷","09":"造紙","10":"鋼鐵","11":"橡膠",
    "12":"汽車","14":"建材營造","15":"航運","16":"觀光餐旅","17":"金融保險",
    "18":"貿易百貨","20":"其他","21":"化學","22":"生技醫療","23":"油電燃氣",
    "24":"半導體","25":"電腦及週邊設備","26":"光電","27":"通信網路",
    "28":"電子零組件","29":"電子通路","30":"資訊服務","31":"其他電子",
    "35":"綠能環保","36":"數位雲端","37":"運動休閒","38":"居家生活","91":"存託憑證",
}


# ---- 日期工具 ----

def roc_to_date(roc_str):
    """民國日期轉 datetime, e.g. '115/05/07' -> datetime(2026,5,7)"""
    roc_str = roc_str.strip()
    m = re.match(r"(\d{2,3})/(\d{2})/(\d{2})", roc_str)
    if not m:
        return None
    year = int(m.group(1)) + 1911
    return datetime(year, int(m.group(2)), int(m.group(3)))


def parse_period(period_str):
    """解析 '115/04/27～115/05/11' -> (start_date, end_date)"""
    parts = re.split(r"[～~\-]", period_str.strip())
    if len(parts) == 2:
        return roc_to_date(parts[0]), roc_to_date(parts[1])
    return None, None


# ---- 資料抓取 ----

def fetch_twse_punish():
    """抓 TWSE 上市處置股"""
    url = "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("stat") != "OK" or not d.get("data"):
        return []
    results = []
    for row in d["data"]:
        code = str(row[2]).strip()
        name = str(row[3]).strip()
        condition = str(row[5]).strip()
        period_str = str(row[6]).strip()
        measure = str(row[7]).strip()
        content = str(row[8]).strip()
        start_date, end_date = parse_period(period_str)
        reason = _extract_reason(content)
        measures = _extract_measures(content)
        results.append({
            "code": code,
            "name": name,
            "market": "上市",
            "condition": condition,
            "measure": measure,
            "start": start_date,
            "end": end_date,
            "period_str": period_str,
            "reason": reason,
            "match_interval": measures["match_interval"],
            "precollect": measures["precollect"],
            "margin": measures["margin"],
        })
    return results


def fetch_tpex_punish():
    """抓 TPEx 上櫃處置股 (OpenAPI)"""
    url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list):
            return []
        results = []
        for row in rows:
            code = row.get("SecuritiesCompanyCode", "").strip()
            name = row.get("CompanyName", "").strip()
            period_raw = row.get("DispositionPeriod", "").strip()
            reason_raw = row.get("DispositionReasons", "").strip()
            content = row.get("DisposalCondition", "").strip()
            # 轉換期間格式: "1150512~1150525" -> "115/05/12～115/05/25"
            period_str = _convert_tpex_period(period_raw)
            start_date, end_date = parse_period(period_str)
            # 判斷處置層級
            if "曾發布處置" in content or "第二次" in content:
                measure = "第二次處置"
            else:
                measure = "第一次處置"
            reason = _extract_reason(content)
            measures = _extract_measures(content)
            results.append({
                "code": code,
                "name": name,
                "market": "上櫃",
                "condition": reason_raw,
                "measure": measure,
                "start": start_date,
                "end": end_date,
                "period_str": period_str,
                "reason": reason,
                "match_interval": measures["match_interval"],
                "precollect": measures["precollect"],
                "margin": measures["margin"],
            })
        return results
    except Exception as e:
        print(f"  TPEx 抓取失敗: {e}")
        return []


def _convert_tpex_period(raw):
    """轉換 TPEx 期間格式: '1150512~1150525' -> '115/05/12～115/05/25'"""
    parts = re.split(r"[~～]", raw.strip())
    converted = []
    for p in parts:
        p = p.strip()
        if len(p) == 7:  # 1150512
            converted.append(f"{p[:3]}/{p[3:5]}/{p[5:7]}")
        else:
            converted.append(p)
    return "～".join(converted)


def _extract_reason(content):
    """從處置內容提取簡要原因"""
    content = content.replace("\n", " ").replace("\r", "")
    if "連續三個營業日" in content:
        reason = "連續3日達注意標準"
    elif "六個營業日" in content or "六次" in content:
        reason = "10日內6次達注意標準"
    elif "當日沖銷" in content:
        reason = "連續3日+當沖標準"
    else:
        reason = "達注意交易資訊標準"
    if "當日沖銷" in content and "連續三" in content:
        reason = "連續3日+當沖標準"
    return reason


def _extract_measures(content):
    """從處置內容解析撮合間隔、預收款券、融資融券限制"""
    content = content.replace("\n", " ").replace("\r", "")

    # 撮合間隔
    m = re.search(r"每(\S+?)分鐘撮合一次", content)
    if m:
        cn_num = m.group(1)
        num_map = {"五": 5, "十": 10, "二十": 20, "三十": 30}
        match_min = num_map.get(cn_num)
        if not match_min:
            try:
                match_min = int(cn_num)
            except ValueError:
                match_min = cn_num
        match_interval = f"{match_min}分鐘"
    else:
        match_interval = ""

    # 預收款券
    if "所有投資人每日委託買賣" in content:
        precollect = "全面預收"
    elif "單筆達十交易單位" in content or "單筆達10交易單位" in content:
        precollect = "條件式預收"
    elif "收取全部之買進價金或賣出證券" in content:
        precollect = "預收"
    else:
        precollect = "無"

    # 融資融券
    if "暫停融券賣出" in content:
        margin = "暫停融券"
    elif "暫停融資買進及融券賣出" in content:
        margin = "暫停融資融券"
    elif "應收足融資自備款或融券保證金" in content:
        margin = "收足自備款"
    else:
        margin = "無限制"

    return {
        "match_interval": match_interval,
        "precollect": precollect,
        "margin": margin,
    }


# ---- 產業分類 ----

def load_sub_industry_csv():
    """從系產業 CSV 載入細產業、所有細產業、產業地位"""
    # 優先找新版 CSV
    csv_path = Path(__file__).parent / "系產業.csv"
    if not csv_path.exists():
        csv_path = Path(__file__).parent / "細產業對應表.csv"
    if not csv_path.exists():
        return {}
    ind_map = {}
    # 嘗試不同編碼
    for enc in ["cp950", "big5", "utf-8-sig", "utf-8"]:
        try:
            with open(csv_path, "r", encoding=enc) as f:
                lines = f.readlines()
            # 找到真正的 header（含「代碼」的那行）
            header_idx = None
            for i, line in enumerate(lines):
                if "代碼" in line:
                    header_idx = i
                    break
            if header_idx is None:
                continue
            import csv as csvmod
            reader = csvmod.reader(lines[header_idx:])
            header = next(reader)
            for row in reader:
                if len(row) < 9:
                    continue
                code = row[1].strip().replace(".TW", "").replace(".TWO", "")
                if not code:
                    continue
                all_subs = row[9].strip() if len(row) > 9 else ""
                position = row[10].strip() if len(row) > 10 else ""
                ind_map[code] = {
                    "industry": row[8].strip(),       # 細產業
                    "all_subs": all_subs,              # 所有細產業
                    "position": position,              # 產業地位
                    "market_cap": "",
                    "full_name": row[2].strip(),       # 商品名
                }
            # 建立每個子產業的全族群股票數
            from collections import Counter as Ctr
            sub_counts = Ctr()
            for v in ind_map.values():
                for s in v.get("all_subs", "").split(","):
                    s = s.strip()
                    if s:
                        sub_counts[s] += 1
            # 把 sub_counts 存在模組層級
            global SUB_INDUSTRY_COUNTS
            SUB_INDUSTRY_COUNTS = dict(sub_counts)
            print(f"  系產業對應表: {len(ind_map)} 筆 (encoding={enc})")
            break
        except (UnicodeDecodeError, StopIteration):
            continue
    return ind_map


def fetch_industry_map():
    """優先用細產業對應表，不足的再從 TWSE/TPEx 補"""
    # 優先載入細產業 CSV
    ind_map = load_sub_industry_csv()
    if ind_map:
        # 驗證：確認是細產業（非粗分類）
        broad_names = set(INDUSTRY_NAMES.values())
        sample_industries = set(v.get("industry","") for v in list(ind_map.values())[:50])
        overlap = sample_industries & broad_names
        if len(overlap) > len(sample_industries) * 0.5:
            print(f"  ⚠ CSV 載入但產業為粗分類，可能欄位錯誤: {overlap}")
        else:
            print(f"  ✓ 產業對應表: {len(ind_map)} 筆 (細產業)")
            return ind_map

    # Fallback: TWSE API（粗分類）
    print("  ⚠ 細產業 CSV 未載入，退回 TWSE/TPEx 粗分類！")
    print(f"    嘗試路徑: {Path(__file__).parent / '系產業.csv'}")
    print(f"    檔案存在: {(Path(__file__).parent / '系產業.csv').exists()}")
    ind_map = {}
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                         headers=HEADERS, timeout=15)
        for c in r.json():
            code = c.get("公司代號", "")
            ind_code = c.get("產業別", "")
            ind_name = INDUSTRY_NAMES.get(ind_code, ind_code)
            ind_map[code] = {
                "industry": ind_name,
                "full_name": c.get("公司名稱", ""),
            }
    except Exception as e:
        print(f"  TWSE 產業資料取得失敗: {e}")
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
                         headers=HEADERS, timeout=15)
        for c in r.json():
            code = c.get("SecuritiesCompanyCode", "")
            ind_code = c.get("SecuritiesIndustryCode", "")
            ind_name = INDUSTRY_NAMES.get(ind_code, ind_code)
            ind_map[code] = {
                "industry": ind_name,
                "full_name": c.get("CompanyName", ""),
            }
    except Exception as e:
        print(f"  TPEx 產業資料取得失敗: {e}")
    return ind_map


# ---- 分析 ----

def deduplicate(records):
    """同一檔股票若有多筆處置（累計二次等），取最晚出關的那筆"""
    best = {}
    all_records = {}  # code -> list of all records
    for r in records:
        code = r["code"]
        if code not in all_records:
            all_records[code] = []
        all_records[code].append(r)
        if code not in best:
            best[code] = r
        else:
            if r["end"] and best[code]["end"] and r["end"] > best[code]["end"]:
                best[code] = r
    # 在 best record 上加累計次數
    for code, rec in best.items():
        rec["total_records"] = len(all_records[code])
        if len(all_records[code]) > 1:
            earliest = min(r["start"] for r in all_records[code] if r["start"])
            rec["first_start"] = earliest
        else:
            rec["first_start"] = rec["start"]
    return list(best.values())


def analyze_clusters(records, industry_map, exit_days=7):
    """分析族群聚集和即將出關（使用所有細產業做交叉比對）"""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 為每筆加產業、產業地位、所有子產業
    for r in records:
        info = industry_map.get(r["code"], {})
        if info:
            r["industry"] = info.get("industry", "未分類")
            r["position"] = info.get("position", "")
            r["market_cap"] = info.get("market_cap", "")
            all_subs_str = info.get("all_subs", "")
            r["all_subs"] = [s.strip() for s in all_subs_str.split(",") if s.strip()] if all_subs_str else []
        elif len(r["code"]) > 4:
            r["industry"] = "衍生商品"
            r["position"] = ""
            r["market_cap"] = ""
            r["all_subs"] = []
        else:
            r["industry"] = "未分類"
            r["position"] = ""
            r["market_cap"] = ""
            r["all_subs"] = []

    # 直接用細產業分群
    real_stocks = [r for r in records if r["industry"] != "衍生商品"]

    # 建立 子產業 -> 處置股 映射（用於交叉關聯顯示）
    sub_to_stocks = defaultdict(set)
    for r in real_stocks:
        for sub in r["all_subs"]:
            sub_to_stocks[sub].add(r["code"])

    # 以細產業為主要分群
    by_industry = defaultdict(list)
    for r in records:
        if r["industry"] == "衍生商品":
            by_industry["衍生商品"].append(r)
        else:
            r["cluster"] = r["industry"]
            # 找出同族群的相關子產業（有其他處置股共享的）
            related = set()
            for sub in r.get("all_subs", []):
                peers = sub_to_stocks.get(sub, set())
                if len(peers) >= 2:
                    related.add(sub)
            r["related_subs"] = sorted(related)
            by_industry[r["industry"]].append(r)

    # 即將出關：end_date 在 exit_days 天內
    exit_cutoff = today + timedelta(days=exit_days)
    upcoming_exits = [r for r in records if r["end"] and today <= r["end"] <= exit_cutoff]
    upcoming_exits.sort(key=lambda x: x["end"])

    # 已出關（end < today 但仍在公告中）
    already_out = [r for r in records if r["end"] and r["end"] < today]

    # 目前仍在處置中
    still_in = [r for r in records if r["end"] and r["end"] >= today]
    still_in.sort(key=lambda x: x["end"])

    # 計算每個族群的全族群總數
    cluster_totals = {}
    for cluster_name in by_industry:
        cluster_totals[cluster_name] = SUB_INDUSTRY_COUNTS.get(cluster_name, 0)

    return {
        "by_industry": dict(by_industry),
        "cluster_totals": cluster_totals,
        "upcoming_exits": upcoming_exits,
        "already_out": already_out,
        "still_in": still_in,
        "exit_cutoff": exit_cutoff,
    }


# ---- 輸出 ----

def print_console_report(records, analysis, exit_days):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"\n{'='*70}")
    print(f"  台股處置股追蹤報告")
    print(f"  查詢時間: {today.strftime('%Y-%m-%d %H:%M')}  |  處置股總數: {len(records)} 檔")
    print(f"{'='*70}")

    # 族群聚集
    print(f"\n■ 族群聚集分析（依產業分類）")
    by_ind = analysis["by_industry"]
    cluster_totals = analysis.get("cluster_totals", {})
    for ind in sorted(by_ind.keys(), key=lambda x: (x == "衍生商品", -len(by_ind[x]))):
        stocks = by_ind[ind]
        if len(stocks) < 2:
            continue
        total = cluster_totals.get(ind, 0)
        total_str = f"族群 {total} 檔，" if total > 0 else ""
        print(f"\n  【{ind}】{total_str}處置 {len(stocks)} 檔:")
        for s in stocks:
            exit_dt = s["end"] + timedelta(days=1) if s["end"] else None; end_str = exit_dt.strftime("%m/%d") if exit_dt else "?"
            days_left = (s["end"] - today).days if s["end"] else "?"
            status = f"剩 {days_left+1} 天" if isinstance(days_left, int) and days_left >= 0 else "已出關"
            mi = s.get("match_interval", "")
            pc = s.get("precollect", "")
            mg = s.get("margin", "")
            print(f"    {s['code']} {s['name']:<8} {s['measure']:<8} {mi:<6} {pc:<8} {mg:<8} 出關 {end_str} ({status})")

    # 即將出關
    print(f"\n■ {exit_days} 天內即將出關")
    if analysis["upcoming_exits"]:
        for s in analysis["upcoming_exits"]:
            exit_dt = s["end"] + timedelta(days=1); end_str = exit_dt.strftime("%Y-%m-%d")
            days_left = (s["end"] - today).days + 1
            print(f"  {s['code']} {s['name']:<8} [{s['industry']}]  出關日: {end_str} (剩 {days_left} 天)")
    else:
        print(f"  （無）")

    # 單獨被處置
    singles = {ind: stocks for ind, stocks in by_ind.items() if len(stocks) == 1}
    if singles:
        print(f"\n■ 單獨被處置（族群無聚集）")
        for ind, stocks in singles.items():
            s = stocks[0]
            exit_dt = s["end"] + timedelta(days=1) if s["end"] else None; end_str = exit_dt.strftime("%m/%d") if exit_dt else "?"
            print(f"  {s['code']} {s['name']:<8} [{ind}]  出關 {end_str} | {s['reason']}")



def _build_html_template():
    """回傳 HTML 模板字串（深色主題，參考 aistockmap.com 風格）"""
    colgroup = """<colgroup>
<col style="width:60px"><col style="width:72px"><col style="width:130px">
<col style="width:80px"><col style="width:55px"><col style="width:65px"><col style="width:68px">
<col style="width:140px"><col style="width:135px"><col style="width:105px">
</colgroup>"""
    colgroup_singles = """<colgroup>
<col style="width:60px"><col style="width:72px"><col style="width:85px"><col style="width:110px">
<col style="width:80px"><col style="width:55px"><col style="width:65px"><col style="width:68px">
<col style="width:140px"><col style="width:135px"><col style="width:105px">
</colgroup>"""
    return """<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>處置股追蹤 {report_date}</title>
<style>
  * {{ box-sizing: border-box; margin:0; padding:0; }}
  body {{ font-family: -apple-system,'Microsoft JhengHei','Noto Sans TC','Segoe UI',sans-serif; background:#0f172a; color:#e2e8f0; font-size:15px; line-height:1.7; }}
  .container {{ max-width:1440px; margin:0 auto; padding:2em 2.5em; }}

  /* Header */
  .header-wrap {{ display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:1em; margin-bottom:1.5em; }}
  .header-left {{ flex:1; min-width:0; }}
  .header-left h1 {{ font-size:2em; font-weight:700; color:#f8fafc; margin-bottom:0.35em; letter-spacing:0.02em; }}
  .header-left h1 span {{ background:linear-gradient(135deg,#a78bfa,#818cf8); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
  .subtitle {{ color:#64748b; font-size:0.9em; }}

  /* Tomorrow Exit Card */
  .tomorrow-exit {{ background:#1e293b; border:1px solid #f87171; border-left:4px solid #ef4444; border-radius:10px; padding:1em 1.3em; min-width:240px; max-width:380px; }}
  .tomorrow-exit .te-title {{ font-weight:700; color:#f87171; font-size:1.05em; margin-bottom:0.5em; display:flex; align-items:center; gap:0.5em; }}
  .tomorrow-exit .te-list {{ list-style:none; }}
  .tomorrow-exit .te-list li {{ padding:5px 0; font-size:0.92em; color:#e2e8f0; border-bottom:1px solid #334155; }}
  .tomorrow-exit .te-list li:last-child {{ border-bottom:none; }}
  .tomorrow-exit .te-list .te-code {{ font-weight:700; color:#fbbf24; margin-right:6px; }}
  .tomorrow-exit .te-list .te-ind {{ color:#94a3b8; font-size:0.8em; margin-left:4px; }}
  .tomorrow-exit .te-none {{ color:#475569; font-size:0.9em; }}
  .tomorrow-exit.empty {{ border-color:#334155; border-left-color:#475569; }}

  /* Stats Row */
  .stats {{ display:flex; gap:0.8em; margin-bottom:2em; flex-wrap:wrap; }}
  .stat-card {{ background:#1e293b; border-radius:10px; padding:1em 1.4em; min-width:140px; flex:1; border:1px solid #334155; }}
  .stat-card .num {{ font-size:2.2em; font-weight:700; line-height:1.2; }}
  .stat-card .label {{ color:#94a3b8; font-size:0.85em; margin-top:4px; }}
  .stat-card.danger .num {{ color:#f87171; }}
  .stat-card.warn .num {{ color:#fbbf24; }}
  .stat-card.info .num {{ color:#60a5fa; }}
  .stat-card.purple .num {{ color:#a78bfa; }}

  /* Section */
  .section {{ margin-bottom:2em; }}
  .section-title {{ font-size:1.25em; font-weight:700; color:#f8fafc; margin-bottom:1em; padding-left:12px; border-left:4px solid #818cf8; }}

  /* Cluster Cards */
  .cluster-card {{ background:#1e293b; border-radius:10px; padding:1.2em 1.5em; margin-bottom:1em; border:1px solid #334155; transition:border-color 0.2s; }}
  .cluster-card:hover {{ border-color:#475569; }}
  .cluster-card h3 {{ margin:0 0 0.8em; font-size:1.1em; color:#f8fafc; }}
  .badge {{ display:inline-block; color:#fff; padding:3px 12px; border-radius:12px; font-size:0.85em; margin-right:8px; font-weight:600; }}

  /* Tables */
  table {{ border-collapse:collapse; width:100%; table-layout:fixed; }}
  thead th {{ background:#0f172a; color:#94a3b8; padding:8px 8px; text-align:left; font-weight:600; font-size:0.9em; white-space:nowrap; overflow:hidden; border-bottom:2px solid #334155; position:sticky; top:0; }}
  tbody td {{ padding:8px 8px; border-bottom:1px solid #2d3a4d; font-size:0.9em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:top; color:#cbd5e1; }}
  tbody td.wrap {{ white-space:normal; word-break:break-all; }}
  tbody tr:hover {{ background:#334155; }}
  .code {{ font-weight:700; color:#fbbf24; font-size:1em; }}

  /* Status Tags */
  .status {{ text-align:center; white-space:nowrap; }}
  .tag-out {{ background:#1e293b; color:#475569; padding:3px 10px; border-radius:4px; font-size:0.82em; border:1px solid #334155; }}
  .tag-soon {{ background:rgba(239,68,68,0.15); color:#f87171; padding:3px 10px; border-radius:4px; font-size:0.82em; font-weight:600; border:1px solid rgba(239,68,68,0.3); }}
  .tag-near {{ background:rgba(251,191,36,0.12); color:#fbbf24; padding:3px 10px; border-radius:4px; font-size:0.82em; border:1px solid rgba(251,191,36,0.25); }}
  .tag-in {{ background:rgba(96,165,250,0.1); color:#60a5fa; padding:3px 10px; border-radius:4px; font-size:0.82em; border:1px solid rgba(96,165,250,0.2); }}
  .highlight {{ background:rgba(251,191,36,0.08) !important; }}
  .highlight td {{ font-weight:600; color:#fbbf24; }}
  .pos {{ font-size:0.85em; color:#94a3b8; }}

  /* Footer */
  .footer {{ margin-top:3em; color:#475569; font-size:0.85em; text-align:center; padding:1.2em 0; border-top:1px solid #1e293b; }}

  .empty-state {{ text-align:center; padding:3em; color:#475569; }}

  /* Related sub-industry tags */
  .related-tag {{ background:#334155; color:#94a3b8; padding:3px 10px; border-radius:4px; font-size:0.82em; display:inline-block; margin:3px 4px 3px 0; }}

  /* Responsive */
  @media (max-width:768px) {{
    .container {{ padding:1em; }}
    .stats {{ gap:0.5em; }}
    .stat-card {{ min-width:100px; padding:0.7em 0.8em; }}
    .stat-card .num {{ font-size:1.6em; }}
    .header-wrap {{ flex-direction:column; }}
    .tomorrow-exit {{ max-width:100%; }}
    table {{ font-size:0.82em; }}
    thead th, tbody td {{ padding:6px; }}
  }}
  @media (min-width:1600px) {{
    .container {{ max-width:1560px; }}
  }}
</style>
<script>
function switchTab(tabName) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector('[data-tab="'+tabName+'"]').classList.add('active');
  document.getElementById('tab-'+tabName).classList.add('active');
}}
</script>
</head><body>
<div class="container">

<div class="header-wrap">
  <div class="header-left">
    <h1><span>台股處置股追蹤</span></h1>
    <p class="subtitle">更新時間: {report_datetime} ｜ 每日 07:00 / 19:00 / 21:00 自動更新 ｜ 資料來源: 證交所/櫃買中心</p>
  </div>
  {tomorrow_exit_html}
</div>

<div class="stats">
  <div class="stat-card danger"><div class="num">{total}</div><div class="label">處置公告總筆數</div></div>
  <div class="stat-card warn"><div class="num">{still_count}</div><div class="label">目前仍在處置</div></div>
  <div class="stat-card info"><div class="num">{exit_soon}</div><div class="label">{exit_days}天內出關</div></div>
  <div class="stat-card purple"><div class="num">{clusters}</div><div class="label">聚集族群數</div></div>
  <div class="stat-card danger"><div class="num">{biggest_cluster}</div><div class="label">最大族群 ({biggest_name})</div></div>
</div>

<div class="section">
<div class="section-title">族群聚集分析</div>
{cluster_cards}
</div>

<div class="section">
<div class="section-title">出關時間軸</div>
<p style="color:#64748b;font-size:0.9em;margin:-0.4em 0 1em 0">同日多檔出關（黃底標示），流動性同時恢復，若題材仍在則資金可能回補。</p>
<div class="cluster-card">
<table>
  <thead><tr><th style="width:90px">出關日</th><th style="width:70px">倒數</th><th style="width:50px">檔數</th><th>個股</th></tr></thead>
  <tbody>{timeline_rows}</tbody>
</table>
</div>
</div>

<div class="section">
<div class="section-title">單獨處置個股</div>
<div class="cluster-card">
<table>
""" + colgroup_singles + """
  <thead><tr><th>代號</th><th>名稱</th><th>產業</th><th>產業地位</th><th>處置層級</th><th>撮合</th><th>預收</th><th>融資融券</th><th>原因</th><th>處置期間</th><th>狀態</th></tr></thead>
  <tbody>{singles_rows}</tbody>
</table>
</div>
</div>

</div>

<p class="footer">本工具僅供資訊追蹤參考，不構成投資建議。處置股交易有額外限制（人工撮合、預收款券），請留意風險。</p>
</div>
</body></html>"""

def generate_html_report(records, analysis, exit_days):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    by_ind = analysis["by_industry"]

    # 族群聚集卡片
    cluster_cards = ""
    cluster_totals = analysis.get("cluster_totals", {})
    for ind in sorted(by_ind.keys(), key=lambda x: (x == "衍生商品", -len(by_ind[x]))):
        stocks = by_ind[ind]
        count = len(stocks)
        if count < 2:
            continue
        total = cluster_totals.get(ind, 0)
        total_str = f'<span style="color:#64748b;font-size:0.85em;margin-left:8px">族群 {total} 檔</span>' if total > 0 else ""
        badge_color = "#dc2626" if count >= 4 else "#f59e0b" if count >= 3 else "#3b82f6"
        rows = ""
        for s in sorted(stocks, key=lambda x: x["end"] or datetime.max):
            end_str = s["end"].strftime("%Y-%m-%d") if s["end"] else "?"
            days_left = (s["end"] - today).days if s["end"] else 999
            # 出關日 = 處置結束日隔天 09:00（結束日當天仍在處置中）
            exit_date = s["end"] + timedelta(days=1) if s["end"] else None
            exit_str = exit_date.strftime("%m/%d") if exit_date else ""
            if days_left < 0:
                status_html = f'<span class="tag-out">{exit_str} 09:00已出關</span>'
            elif days_left == 0:
                status_html = f'<span class="tag-soon">明日 {exit_str} 09:00出關</span>'
            elif days_left <= 3:
                status_html = f'<span class="tag-soon">剩 {days_left+1} 天 ({exit_str}出關)</span>'
            elif days_left <= 7:
                status_html = f'<span class="tag-near">剩 {days_left+1} 天 ({exit_str}出關)</span>'
            else:
                status_html = f'<span class="tag-in">剩 {days_left+1} 天</span>'
            multi_name = ""
            multi_reason = f' (累計{s["total_records"]}次)' if s["total_records"] > 1 else ""
            pos = s.get("position", "")
            cap = s.get("market_cap", "")
            cap_html = f'{cap}億' if cap else ""
            mi = s.get("match_interval", "")
            pc = s.get("precollect", "")
            mg = s.get("margin", "")
            pc_cls = ' class="tag-soon"' if pc == "全面預收" else ""
            rows += f'<tr><td class="code">{s["code"]}</td><td>{s["name"]}</td><td class="pos wrap" title="{pos}">{pos}</td><td>{s["measure"]}{multi_reason}</td><td>{mi}</td><td><span{pc_cls}>{pc}</span></td><td>{mg}</td><td class="wrap">{s["reason"]}</td><td>{s["period_str"]}</td><td class="status">{status_html}</td></tr>\n'
        # 收集此族群的相關子產業
        all_related = set()
        for s in stocks:
            all_related.update(s.get("related_subs", []))
        all_related.discard(ind)
        related_tags = ""
        if all_related:
            tags = " ".join(f'<span class="related-tag">{t}</span>' for t in sorted(all_related)[:6])
            related_tags = f'<div style="margin-top:4px">{tags}</div>'
        cluster_cards += f'''
<div class="cluster-card">
  <h3><span class="badge" style="background:{badge_color}">處置 {count}</span> {ind}{total_str}</h3>{related_tags}
  <table>
    <colgroup><col style="width:60px"><col style="width:72px"><col style="width:130px"><col style="width:80px"><col style="width:55px"><col style="width:65px"><col style="width:68px"><col style="width:140px"><col style="width:135px"><col style="width:105px"></colgroup>
    <thead><tr><th>代號</th><th>名稱</th><th>產業地位</th><th>處置層級</th><th>撮合</th><th>預收</th><th>融資融券</th><th>原因</th><th>處置期間</th><th>狀態</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>'''

    # 單獨處置
    singles_rows = ""
    for ind in sorted(by_ind.keys()):
        stocks = by_ind[ind]
        if len(stocks) != 1:
            continue
        s = stocks[0]
        days_left = (s["end"] - today).days if s["end"] else 999
        exit_date = s["end"] + timedelta(days=1) if s["end"] else None
        exit_str = exit_date.strftime("%m/%d") if exit_date else ""
        if days_left < 0:
            # exit date already computed above
            status_html = f'<span class="tag-out">{exit_str} 09:00已出關</span>'
        elif days_left == 0:
            status_html = f'<span class="tag-soon">明日 {exit_str} 09:00出關</span>'
        elif days_left <= 3:
            status_html = f'<span class="tag-soon">剩 {days_left+1} 天 ({exit_str}出關)</span>'
        else:
            status_html = f'<span class="tag-in">剩 {days_left+1} 天</span>'
        pos = s.get("position", "")
        cap = s.get("market_cap", "")
        cap_html = f'{cap}億' if cap else ""
        mi = s.get("match_interval", "")
        pc = s.get("precollect", "")
        mg = s.get("margin", "")
        pc_cls = ' class="tag-soon"' if pc == "全面預收" else ""
        singles_rows += f'<tr><td class="code">{s["code"]}</td><td>{s["name"]}</td><td>{ind}</td><td class="pos wrap" title="{pos}">{pos}</td><td>{s["measure"]}</td><td>{mi}</td><td><span{pc_cls}>{pc}</span></td><td>{mg}</td><td class="wrap">{s["reason"]}</td><td>{s["period_str"]}</td><td class="status">{status_html}</td></tr>\n'

    # 出關時間軸（顯示出關日 = 處置結束日+1天）
    timeline_rows = ""
    exit_groups = defaultdict(list)
    for s in analysis["still_in"]:
        if s["end"]:
            exit_date = s["end"] + timedelta(days=1)
            exit_groups[exit_date.strftime("%Y-%m-%d")].append(s)
    for date_str in sorted(exit_groups.keys()):
        stocks = exit_groups[date_str]
        days_left = (datetime.strptime(date_str, "%Y-%m-%d") - today).days
        names = ", ".join(f'{s["code"]} {s["name"]}({s["industry"]})' for s in stocks)
        count = len(stocks)
        highlight = ' class="highlight"' if count >= 2 else ""
        countdown = f"剩 {days_left} 天" if days_left > 0 else "明日出關" if days_left == 0 else "已出關"
        timeline_rows += f'<tr{highlight}><td>{date_str}</td><td>{countdown}</td><td><strong>{count}</strong> 檔</td><td>{names}</td></tr>\n'

    # 明日出關卡片
    tomorrow = today + timedelta(days=1)
    tomorrow_stocks = []
    for s in analysis["still_in"]:
        if s["end"]:
            exit_date = s["end"] + timedelta(days=1)
            if exit_date.date() == tomorrow.date():
                tomorrow_stocks.append(s)
    if tomorrow_stocks:
        te_items = ""
        for s in sorted(tomorrow_stocks, key=lambda x: x.get("industry", "")):
            te_items += f'<li><span class="te-code">{s["code"]}</span>{s["name"]} <span class="te-ind">{s.get("industry","")}</span></li>\n'
        tomorrow_exit_html = f'''<div class="tomorrow-exit">
  <div class="te-title">&#x1F514; 明日出關 ({tomorrow.strftime("%m/%d")}) \u2014 {len(tomorrow_stocks)} 檔</div>
  <ul class="te-list">{te_items}</ul>
</div>'''
    else:
        tomorrow_exit_html = f'''<div class="tomorrow-exit empty">
  <div class="te-title" style="color:#475569;">明日出關 ({tomorrow.strftime("%m/%d")})</div>
  <span class="te-none">明日無出關個股</span>
</div>'''

    # 統計
    total = len(records)
    still_count = len(analysis["still_in"])
    exit_soon = len(analysis["upcoming_exits"])
    clusters = sum(1 for ind, st in by_ind.items() if len(st) >= 2)
    # 最大族群排除衍生商品（雜項兜底無分析價值）
    real_clusters = {ind: st for ind, st in by_ind.items() if ind != "衍生商品" and len(st) >= 2}
    biggest_cluster = max((len(st) for st in real_clusters.values()), default=0) if real_clusters else 0
    biggest_name = [ind for ind, st in real_clusters.items() if len(st) == biggest_cluster]

    template = _build_html_template()
    return template.format(
        report_date=today.strftime("%Y-%m-%d"),
        report_datetime=today.strftime("%Y-%m-%d %H:%M"),
        total=total,
        still_count=still_count,
        exit_soon=exit_soon,
        exit_days=exit_days,
        clusters=clusters,
        biggest_cluster=biggest_cluster,
        biggest_name=", ".join(biggest_name[:1]),
        cluster_cards=cluster_cards,
        timeline_rows=timeline_rows,
        singles_rows=singles_rows,
        tomorrow_exit_html=tomorrow_exit_html,
    )


# ---- GitHub Pages 部署 ----

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = "kuhper/punish-stock-report"


def _github_upload(file_path, repo_path, commit_msg):
    """上傳單一檔案到 GitHub repo"""
    import base64
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{repo_path}"
    headers_gh = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    content = Path(file_path).read_text(encoding="utf-8")
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")

    sha = None
    try:
        r = requests.get(api_url, headers=headers_gh, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    payload = {"message": commit_msg, "content": content_b64}
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers_gh, json=payload, timeout=30)
    r.raise_for_status()
    return True


def deploy_to_github(html_path):
    """將 HTML 報告部署到 GitHub Pages（punish.html + portal index.html）"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("  GitHub 部署跳過：未設定 GITHUB_TOKEN 或 GITHUB_REPO")
        return False

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    try:
        # 1. 上傳處置股報告為 punish.html
        _github_upload(html_path, "punish.html", f"更新處置股報告 {now_str}")
        print(f"  punish.html 部署成功")

        # 2. 上傳 portal 首頁（優先用 portal_deploy.html，避免截斷問題）
        portal_dir = Path(html_path).parent
        portal_path = portal_dir / "portal_deploy.html"
        if not portal_path.exists():
            portal_path = portal_dir / "portal.html"
        if portal_path.exists():
            _github_upload(portal_path, "index.html", f"更新入口頁 {now_str}")
            print(f"  index.html (portal) 部署成功")

        page_url = f"https://{GITHUB_REPO.split('/')[0]}.github.io/{GITHUB_REPO.split('/')[1]}/"
        print(f"  GitHub Pages: {page_url}")
        return True
    except Exception as e:
        print(f"  GitHub Pages 部署失敗: {e}")
        return False


# ---- Telegram 推播 ----

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")


def build_tg_message(records, analysis, exit_days):
    """組裝 Telegram 純文字摘要"""
    today = datetime.now()
    by_ind = analysis["by_industry"]
    still_count = len(analysis["still_in"])
    exit_soon = len(analysis["upcoming_exits"])

    lines = []
    lines.append(f"📋 <b>處置股日報</b>  {today.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"處置中 <b>{still_count}</b> 檔 ｜ {exit_days}天內出關 <b>{exit_soon}</b> 檔")
    lines.append("")

    # 族群聚集
    cluster_found = False
    cluster_totals = analysis.get("cluster_totals", {})
    for ind in sorted(by_ind.keys(), key=lambda x: (x == "衍生商品", -len(by_ind[x]))):
        stocks = by_ind[ind]
        if len(stocks) < 2:
            continue
        if not cluster_found:
            lines.append("🔥 <b>族群聚集</b>")
            cluster_found = True
        total = cluster_totals.get(ind, 0)
        total_str = f"/{total}" if total > 0 else ""
        names = " / ".join(
            f'{s["code"]}{s["name"]}({s["match_interval"]},{s["precollect"]})'
            for s in stocks
        )
        lines.append(f"  <b>{ind}</b> [處置{len(stocks)}{total_str}檔] {names}")
    if cluster_found:
        lines.append("")

    # 即將出關（3天內）
    soon = [r for r in records if r["end"] and 0 <= (r["end"] - today).days <= 3]
    if soon:
        lines.append("⏰ <b>3天內出關</b>")
        for s in sorted(soon, key=lambda x: x["end"]):
            d = (s["end"] - today).days
            ind = s.get("industry", "")
            lines.append(f"  {s['code']} {s['name']} [{ind}] {s['match_interval']} {s['precollect']} → {d}天後出關")
        lines.append("")

    # 全面預收的（交易摩擦最大）
    full_pre = [r for r in records if r.get("precollect") == "全面預收" and r["end"] and (r["end"] - today).days >= 0]
    if full_pre:
        lines.append(f"🚫 <b>全面預收</b> ({len(full_pre)}檔)")
        for s in sorted(full_pre, key=lambda x: x["end"]):
            d = (s["end"] - today).days
            lines.append(f"  {s['code']} {s['name']} [{s.get('industry','')}] 20分撮合 → {d}天後出關")

    return "\n".join(lines)


def send_telegram(text):
    """透過 Telegram Bot API 發送訊息"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("  Telegram 推播成功")
    except Exception as e:
        print(f"  Telegram 推播失敗: {e}")


def send_telegram_document(file_path, caption=""):
    """透過 Telegram Bot API 發送檔案"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            mime = "application/pdf" if str(file_path).endswith(".pdf") else "text/html"
            files = {"document": (Path(file_path).name, f, mime)}
            data = {"chat_id": TG_CHAT_ID}
            if caption:
                data["caption"] = caption
            r = requests.post(url, data=data, files=files, timeout=30)
            r.raise_for_status()
            print(f"  Telegram 檔案發送成功: {Path(file_path).name}")
    except Exception as e:
        print(f"  Telegram 檔案發送失敗 ({Path(file_path).name}): {e}")



def generate_pdf_report(html_path, pdf_path):
    """將 HTML 報告轉成 PDF（A4 橫式）"""
    try:
        from weasyprint import HTML, CSS
    except ImportError:
        print("  weasyprint 未安裝，跳過 PDF 產生")
        return False

    pdf_css = CSS(string="""
@page {
    size: A4 landscape;
    margin: 0.8cm;
}
body {
    font-family: 'Droid Sans Fallback', sans-serif !important;
    font-size: 9px !important;
}
.container {
    max-width: 100% !important;
    padding: 5px !important;
}
table {
    font-size: 8px !important;
}
th, td {
    padding: 3px 4px !important;
}
h1 { font-size: 16px !important; }
h2 { font-size: 13px !important; }
.cluster-card {
    page-break-inside: avoid;
}
""")

    try:
        html = HTML(filename=str(html_path))
        html.write_pdf(str(pdf_path), stylesheets=[pdf_css], presentational_hints=True)
        print(f"  PDF 報告: {pdf_path}")
        return True
    except Exception as e:
        print(f"  PDF 產生失敗: {e}")
        return False


# ---- Main ----

def main():
    parser = argparse.ArgumentParser(description="台股處置股追蹤")
    parser.add_argument("--days", type=int, default=3, help="即將出關天數範圍 (預設 7)")
    parser.add_argument("--no-html", action="store_true", help="不產生 HTML 報告")
    parser.add_argument("--tg", action="store_true", help="發送 Telegram 推播")
    parser.add_argument("--deploy", action="store_true", help="部署到 GitHub Pages")
    args = parser.parse_args()

    print("抓取處置股資料...")
    sys.stdout.flush()

    twse = fetch_twse_punish()
    print(f"  上市: {len(twse)} 筆")
    tpex = fetch_tpex_punish()
    print(f"  上櫃: {len(tpex)} 筆")

    all_records = twse + tpex

    print("取得產業分類...")
    sys.stdout.flush()
    ind_map = fetch_industry_map()
    print(f"  共 {len(ind_map)} 家公司資料")

    # 去重（同股票多筆取最晚出關）
    deduped = deduplicate(all_records)
    print(f"去重後: {len(deduped)} 檔處置股")

    # 分析
    analysis = analyze_clusters(deduped, ind_map, exit_days=args.days)

    # Console 報告
    print_console_report(deduped, analysis, args.days)

    # HTML 報告
    if not args.no_html:
        html = generate_html_report(all_records, analysis, args.days)
        date_str = datetime.now().strftime("%Y%m%d")
        report_path = DATA_DIR / f"punish_{date_str}.html"
        report_path.write_text(html, encoding="utf-8")
        print(f"\nHTML 報告: {report_path}")

        # PDF 報告
        pdf_path = DATA_DIR / f"punish_{date_str}.pdf"
        generate_pdf_report(report_path, pdf_path)

    # Telegram 推播
    if args.tg:
        tg_msg = build_tg_message(deduped, analysis, args.days)
        send_telegram(tg_msg)
        if not args.no_html:
            # 傳送 HTML
            if report_path.exists():
                send_telegram_document(report_path, caption=f"處置股日報 {date_str}")
            # 傳送 PDF
            if pdf_path.exists():
                send_telegram_document(pdf_path, caption=f"處置股日報 PDF {date_str}")

    # GitHub Pages 部署
    if args.deploy and not args.no_html:
        deploy_to_github(report_path)

    # 存 CSV 歷史
    csv_path = DATA_DIR / "punish_history.csv"
    fieldnames = ["query_date", "code", "name", "market", "industry", "measure",
                  "condition", "reason", "start", "end", "period_str"]
    file_exists = csv_path.exists()
    today_str = datetime.now().strftime("%Y-%m-%d")
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for r in deduped:
            writer.writerow({
                "query_date": today_str,
                "code": r["code"],
                "name": r["name"],
                "market": r["market"],
                "industry": r.get("industry", ""),
                "measure": r["measure"],
                "condition": r["condition"],
                "reason": r["reason"],
                "start": r["start"].strftime("%Y-%m-%d") if r["start"] else "",
                "end": r["end"].strftime("%Y-%m-%d") if r["end"] else "",
                "period_str": r["period_str"],
            })
    print(f"歷史紀錄: {csv_path}")


if __name__ == "__main__":
    main()

