#!/usr/bin/env python3
"""
美股昨日強勢股追蹤
=================
按市值分三類（千億/百億~千億/10億~百億美元），依漲幅排行。
"""

import requests, json, sys, argparse, time, base64, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
import yfinance as yf
import pandas as pd

DATA_DIR = Path(__file__).parent / "us_data"
TZ_TW = timezone(timedelta(hours=8))
DATA_DIR.mkdir(exist_ok=True)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = "kuhper/punish-stock-report"

HEADERS_REQ = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# ---- 中文名稱對照 ----
CN_NAMES = {
    "AAPL":"蘋果","MSFT":"微軟","GOOGL":"谷歌-A","GOOG":"谷歌-C","AMZN":"亞馬遜",
    "NVDA":"輝達","META":"Meta","TSLA":"特斯拉","BRK-B":"波克夏-B","AVGO":"博通",
    "TSM":"台積電ADR","LLY":"禮來","WMT":"沃爾瑪","JPM":"摩根大通","V":"Visa",
    "UNH":"聯合健康","MA":"萬事達卡","XOM":"艾克森美孚","COST":"好市多","HD":"家得寶",
    "PG":"寶僑","JNJ":"嬌生","ABBV":"艾伯維","NFLX":"網飛","CRM":"Salesforce",
    "BAC":"美國銀行","ORCL":"甲骨文","CVX":"雪佛龍","MRK":"默克","KO":"可口可樂",
    "ADBE":"Adobe","AMD":"超微","PEP":"百事可樂","TMO":"賽默飛","CSCO":"思科",
    "ACN":"埃森哲","LIN":"林德","MCD":"麥當勞","ABT":"亞培","WFC":"富國銀行",
    "DHR":"丹納赫","TXN":"德州儀器","PM":"菲利普莫里斯","ISRG":"直覺外科",
    "NEE":"NextEra能源","INTU":"Intuit","QCOM":"高通","GE":"奇異",
    "AMGN":"安進","AMAT":"應材","CAT":"開拓重工","PFE":"輝瑞","BKNG":"Booking",
    "AXP":"美國運通","RTX":"雷神","HON":"漢威聯合","UBER":"優步","LOW":"勞氏",
    "T":"AT&T","GS":"高盛","SPGI":"標普全球","SYK":"史賽克","BLK":"貝萊德",
    "VRTX":"Vertex","DE":"迪爾","C":"花旗","UNP":"聯合太平洋","PLD":"Prologis",
    "BA":"波音","ADP":"ADP","SCHW":"嘉信理財","MDLZ":"億滋","BSX":"波士頓科學",
    "LMT":"洛克希德馬丁","GILD":"吉利德","MMC":"達信","FI":"Fiserv",
    "IBM":"IBM","PANW":"Palo Alto","CB":"Chubb","MO":"奧馳亞","SO":"南方電力",
    "DUK":"杜克能源","SBUX":"星巴克","ICE":"洲際交易所","CL":"高露潔",
    "CME":"芝商所","SHW":"宣偉","TGT":"塔吉特","PH":"派克漢尼汾",
    "MCO":"穆迪","BDX":"BD醫療","NOC":"諾斯洛普","ITW":"伊利諾工具",
    "USB":"美國合眾銀行","ZTS":"碩騰","FDX":"聯邦快遞","EQIX":"Equinix",
    "REGN":"再生元","MMM":"3M","GD":"通用動力","APD":"空氣化工",
    "CSX":"CSX運輸","TJX":"TJX","NSC":"諾福克南方","KLAC":"科磊",
    "SNPS":"新思科技","CDNS":"益華電腦","LRCX":"科林研發","MRVL":"邁威爾",
    "INTC":"英特爾","MU":"美光","ASML":"阿斯麥","ARM":"Arm Holdings",
    "BABA":"阿里巴巴","PDD":"拼多多","JD":"京東","BIDU":"百度","NIO":"蔚來",
    "XPEV":"小鵬","LI":"理想","TME":"騰訊音樂","BILI":"嗶哩嗶哩","NTES":"網易",
    "TCOM":"攜程","ZTO":"中通快遞","WB":"微博","VIPS":"唯品會","TAL":"好未來",
    "EDU":"新東方","KC":"金山雲","FUTU":"富途","TIGR":"老虎證券",
    "TM":"豐田汽車","SONY":"索尼","SHOP":"Shopify","SQ":"Block",
    "SNOW":"Snowflake","PLTR":"Palantir","COIN":"Coinbase","DDOG":"Datadog",
    "NET":"Cloudflare","ZS":"Zscaler","CRWD":"CrowdStrike","TEAM":"Atlassian",
    "WDAY":"Workday","OKTA":"Okta","MDB":"MongoDB","U":"Unity",
    "RIVN":"Rivian","LCID":"Lucid","F":"福特","GM":"通用汽車",
    "DIS":"迪士尼","CMCSA":"康卡斯特","WBD":"華納探索","PARA":"派拉蒙",
    "NKE":"耐吉","LULU":"露露檸檬","ABNB":"Airbnb","DASH":"DoorDash",
    "SLB":"斯倫貝謝","OXY":"西方石油","COP":"康菲石油","EOG":"EOG能源",
    "DVN":"德文能源","HAL":"哈利伯頓","WMB":"威廉姆斯","BHP":"必和必拓",
    "RIO":"力拓","VALE":"淡水河谷","NUE":"紐柯鋼鐵","FCX":"自由港",
    "ADI":"亞德諾","ON":"安森美","MCHP":"微芯科技","NXPI":"恩智浦",
    "MPWR":"Monolithic","SWKS":"思佳訊","WOLF":"Wolfspeed",
    "SMCI":"超微電腦","DELL":"戴爾","HPQ":"惠普","ANET":"Arista",
    "MSCI":"MSCI","FTNT":"飛塔網路","NOW":"ServiceNow","VEEV":"Veeva",
    "TWLO":"Twilio","TTD":"The Trade Desk","RBLX":"Roblox",
    "SE":"Sea冬海","GRAB":"Grab","MELI":"美卡多","NU":"Nu Holdings",
    "SPOT":"Spotify","PYPL":"PayPal","SAN":"桑坦德","HSBC":"匯豐",
    "UBS":"瑞銀","DB":"德意志銀行","CS":"瑞信","STLA":"Stellantis",
    "HMC":"本田","RACE":"法拉利","SAP":"SAP","INFY":"Infosys",
    "WIT":"Wipro","IBN":"ICICI銀行","HDB":"HDFC銀行",
    "PBR":"巴西石油","ITUB":"伊塔烏","BBD":"巴西銀行",
    "GFL":"GFL環保","GOLD":"巴里克黃金","NEM":"紐蒙特黃金",
    "ABNB":"Airbnb","LYFT":"Lyft","ZM":"Zoom","DOCU":"DocuSign",
    "ROKU":"Roku","PINS":"Pinterest","SNAP":"Snap","ETSY":"Etsy",
    "CEG":"星座能源","VST":"Vistra","GEV":"GE Vernova","CARR":"開利",
    "TT":"Trane","APH":"安費諾","FICO":"FICO","CPRT":"Copart",
    "CTAS":"信達思","ODFL":"Old Dominion","FAST":"Fastenal",
    "ELV":"Elevance","HUM":"Humana","CI":"信諾","CNC":"Centene",
    "MOH":"Molina","MCK":"麥克森","CAH":"Cardinal","ABC":"Cencora",
    "UPS":"UPS","UBER":"優步","DASH":"DoorDash",
    "ENPH":"Enphase","SEDG":"SolarEdge","FSLR":"第一太陽能",
    "TRGP":"Targa","ET":"能源轉移","KMI":"金德摩根","OKE":"ONEOK",
    "AIG":"AIG","MET":"大都會人壽","PRU":"保德信","AFL":"Aflac",
    "ALL":"好事達","TRV":"旅行者","PGR":"Progressive",
    "CMG":"Chipotle","YUM":"百勝","SBUX":"星巴克","DPZ":"達美樂",
    "DXCM":"DexCom","ILMN":"Illumina","A":"安捷倫","ALGN":"隱適美",
    "EW":"愛德華茲","IDXX":"IDEXX","MTD":"梅特勒",
    "VRT":"維諦技術","EMR":"艾默生","ROK":"洛克威爾","AME":"阿美特克",
    "KVUE":"Kenvue","CTVA":"科迪華","DD":"杜邦","DOW":"陶氏",
    "LYB":"利安德巴塞爾","PPG":"PPG工業","ECL":"藝康",
    "ARES":"Ares管理","KKR":"KKR","APO":"阿波羅","BX":"黑石",
    "CG":"凱雷","TPG":"TPG","OWL":"Blue Owl","BAM":"布魯克菲爾德",
}


# ---- 產業地位覆蓋（手動維護重點個股）----
POSITION_OVERRIDE = {
    # 半導體
    "NVDA": "AI晶片龍頭",
    "TSM": "全球晶圓代工龍頭",
    "AVGO": "AI網通/客製晶片龍頭",
    "AMD": "CPU/GPU第二大廠",
    "INTC": "x86 CPU老牌巨頭",
    "QCOM": "行動通訊晶片龍頭",
    "TXN": "類比IC龍頭",
    "MU": "DRAM/NAND大廠",
    "MRVL": "數據基礎設施晶片",
    "ARM": "行動架構授權龍頭",
    "ASML": "EUV光刻機獨占",
    "AMAT": "半導體設備龍頭",
    "LRCX": "蝕刻設備龍頭",
    "KLAC": "半導體檢測設備",
    "ADI": "高效能類比IC",
    "ON": "車用/工控半導體",
    "NXPI": "車用半導體龍頭",
    "MCHP": "嵌入式微控制器",
    "SMCI": "AI伺服器組裝",
    "SNPS": "EDA三巨頭",
    "CDNS": "EDA三巨頭",
    "MPWR": "電源管理IC",
    "SWKS": "射頻前端晶片",
    # 科技巨頭
    "AAPL": "消費電子/生態系龍頭",
    "MSFT": "企業軟體/雲端龍頭",
    "GOOGL": "搜尋/廣告/雲端",
    "AMZN": "電商/雲端AWS龍頭",
    "META": "社群平台/AI廣告",
    "TSLA": "電動車/自駕龍頭",
    "NFLX": "串流影音龍頭",
    "ORCL": "企業資料庫/雲端",
    # 雲端/SaaS
    "CRM": "CRM SaaS龍頭",
    "NOW": "IT服務管理SaaS",
    "PLTR": "數據分析/AI平台",
    "SNOW": "雲端數據倉儲",
    "CRWD": "端點資安龍頭",
    "PANW": "網路資安龍頭",
    "FTNT": "資安設備龍頭",
    "ZS": "零信任資安",
    "NET": "CDN/邊緣運算",
    "DDOG": "雲端監控SaaS",
    "MDB": "NoSQL資料庫",
    "WDAY": "人資ERP SaaS",
    "SHOP": "電商SaaS平台",
    "COIN": "加密貨幣交易所",
    "ANET": "數據中心網路交換器",
    "DELL": "企業PC/伺服器",
    # 網路硬體
    "CSCO": "企業網路設備龍頭",
    # 金融
    "JPM": "美國最大銀行",
    "V": "支付網路雙龍頭",
    "MA": "支付網路雙龍頭",
    "GS": "投資銀行龍頭",
    "BLK": "全球最大資產管理",
    "BX": "另類資產管理龍頭",
    "KKR": "PE私募股權巨頭",
    "SPGI": "信評/數據分析",
    "MSCI": "指數/風險分析",
    "CME": "全球最大期貨交易所",
    "ICE": "交易所/數據服務",
    # 醫療
    "LLY": "GLP-1減重藥龍頭",
    "UNH": "美國最大醫療保險",
    "ABBV": "免疫藥物大廠",
    "JNJ": "多角化醫療巨頭",
    "MRK": "癌症免疫療法K藥",
    "TMO": "生命科學儀器龍頭",
    "ISRG": "手術機器人龍頭",
    "VRTX": "罕見病/基因療法",
    "REGN": "抗體藥物大廠",
    "DXCM": "連續血糖監測",
    "IDXX": "動物診斷龍頭",
    # 消費
    "WMT": "全球最大零售商",
    "COST": "會員制量販龍頭",
    "HD": "家居建材零售龍頭",
    "MCD": "全球速食龍頭",
    "SBUX": "全球咖啡連鎖龍頭",
    "NKE": "運動品牌龍頭",
    "LULU": "高端瑜伽運動服飾",
    "KO": "飲料品牌龍頭",
    "PEP": "飲料/零食巨頭",
    "PG": "日用消費品龍頭",
    "CMG": "墨西哥快餐連鎖",
    # 工業
    "CAT": "重型機械龍頭",
    "DE": "農業機械龍頭",
    "HON": "工業自動化巨頭",
    "GE": "航空發動機/能源",
    "BA": "民航客機雙龍頭",
    "RTX": "軍工/航太巨頭",
    "LMT": "軍工龍頭",
    "UPS": "快遞物流巨頭",
    "UBER": "共乘/外送平台龍頭",
    "ABNB": "短租平台龍頭",
    # 能源
    "XOM": "西方石油巨頭",
    "CVX": "綜合能源巨頭",
    "COP": "獨立油氣探勘龍頭",
    "CEG": "美國最大核能發電",
    "VST": "AI算力電力供應商",
    "NEE": "全球最大風電/太陽能",
    # 中概/ADR
    "BABA": "中國電商/雲端龍頭",
    "PDD": "拼多多/Temu跨境電商",
    "JD": "中國自營電商/物流",
    "BIDU": "中國搜尋/AI",
    "NIO": "中國高端電動車",
    "XPEV": "中國智能電動車",
    "LI": "中國增程式電動車",
    "NTES": "中國遊戲/音樂",
    "TCOM": "中國線上旅遊龍頭",
    "BILI": "中國Z世代影音社群",
    "SE": "東南亞電商/支付",
    "GRAB": "東南亞超級App",
    "MELI": "拉美電商龍頭",
    "NU": "拉美數位銀行",
    # 原物料
    "BHP": "全球最大礦商",
    "RIO": "鐵礦石/鋁業巨頭",
    "FCX": "全球最大銅礦商",
    "NEM": "全球最大金礦商",
    "VALE": "鐵礦石龍頭",
    "NUE": "美國鋼鐵龍頭",
    # 不動產
    "PLD": "物流倉儲REIT龍頭",
    "EQIX": "數據中心REIT龍頭",
    "AMT": "通訊基站REIT龍頭",
    # 軟體/其他
    "ADBE": "創意/行銷軟體龍頭",
    "INTU": "消費稅務/會計軟體",
    "SAP": "歐洲企業ERP龍頭",
    "IBM": "企業AI/混合雲",
    "PYPL": "線上支付平台",
    "SPOT": "音樂串流龍頭",
    "DIS": "娛樂/主題樂園巨頭",
    "RACE": "超跑奢侈品牌",
    "TM": "全球最大車廠",
    "SONY": "遊戲/影音/感測器",
    "VRT": "AI數據中心散熱/電力",
    "APH": "連接器/感測器龍頭",
    "FSLR": "美國太陽能面板龍頭",
    "GEV": "風電/能源轉型設備",
    "WOLF": "碳化矽功率元件",
}

# ---- Yahoo Finance 產業英翻中 ----
INDUSTRY_CN = {
    "Semiconductors": "半導體",
    "Semiconductor Equipment & Materials": "半導體設備",
    "Software - Infrastructure": "基礎設施軟體",
    "Software - Application": "應用軟體",
    "Internet Content & Information": "網路內容",
    "Internet Retail": "網路零售",
    "Consumer Electronics": "消費電子",
    "Information Technology Services": "IT服務",
    "Electronic Components": "電子零組件",
    "Computer Hardware": "電腦硬體",
    "Communication Equipment": "通訊設備",
    "Scientific & Technical Instruments": "科學儀器",
    "Auto Manufacturers": "汽車製造",
    "Banks - Diversified": "綜合銀行",
    "Banks - Regional": "區域銀行",
    "Capital Markets": "資本市場",
    "Credit Services": "信用服務",
    "Insurance - Diversified": "綜合保險",
    "Financial Data & Stock Exchanges": "金融數據/交易所",
    "Asset Management": "資產管理",
    "Drug Manufacturers - General": "大型製藥",
    "Drug Manufacturers - Specialty & Generic": "專科/學名藥",
    "Biotechnology": "生物科技",
    "Medical Devices": "醫療器材",
    "Medical Instruments & Supplies": "醫療器材",
    "Health Care Plans": "醫療保險",
    "Diagnostics & Research": "診斷研究",
    "Discount Stores": "折扣零售",
    "Home Improvement Retail": "居家建材零售",
    "Specialty Retail": "專業零售",
    "Restaurants": "餐飲",
    "Beverages - Non-Alcoholic": "非酒精飲料",
    "Household & Personal Products": "家庭用品",
    "Apparel Manufacturing": "服飾製造",
    "Footwear & Accessories": "鞋類配件",
    "Packaged Foods": "包裝食品",
    "Aerospace & Defense": "航太國防",
    "Farm & Heavy Construction Machinery": "重型機械",
    "Diversified Industrials": "多角化工業",
    "Integrated Freight & Logistics": "物流運輸",
    "Railroads": "鐵路運輸",
    "Oil & Gas Integrated": "綜合油氣",
    "Oil & Gas E&P": "油氣探勘",
    "Oil & Gas Equipment & Services": "油氣服務",
    "Utilities - Regulated Electric": "電力公用",
    "Utilities - Renewable": "再生能源",
    "Solar": "太陽能",
    "Residential Construction": "住宅建設",
    "REIT - Industrial": "工業REIT",
    "REIT - Specialty": "特殊REIT",
    "REIT - Diversified": "綜合REIT",
    "Gold": "黃金",
    "Copper": "銅礦",
    "Steel": "鋼鐵",
    "Other Industrial Metals & Mining": "工業金屬",
    "Entertainment": "娛樂",
    "Electronic Gaming & Multimedia": "電子遊戲",
    "Travel Services": "旅遊服務",
    "Lodging": "住宿",
    "Gambling": "博弈",
    "Tobacco": "菸草",
    "Luxury Goods": "奢侈品",
}


def get_stock_universe():
    """取得美股宇宙（S&P 500 核心 + 額外重要個股）"""
    # S&P 500 核心成分股（硬編碼，避免 Wikipedia 依賴）
    sp500_core = [
        "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","BRK-B","AVGO",
        "LLY","WMT","JPM","V","UNH","MA","XOM","COST","HD","PG","JNJ","ABBV",
        "NFLX","CRM","BAC","ORCL","CVX","MRK","KO","ADBE","AMD","PEP","TMO",
        "CSCO","ACN","LIN","MCD","ABT","WFC","DHR","TXN","PM","ISRG","NEE",
        "INTU","QCOM","GE","AMGN","AMAT","CAT","PFE","BKNG","AXP","RTX","HON",
        "UBER","LOW","T","GS","SPGI","SYK","BLK","VRTX","DE","C","UNP","PLD",
        "BA","ADP","SCHW","MDLZ","BSX","LMT","GILD","MMC","FI","IBM","PANW",
        "CB","MO","SO","DUK","SBUX","ICE","CL","CME","SHW","TGT","PH","MCO",
        "BDX","NOC","ITW","USB","ZTS","FDX","EQIX","REGN","MMM","GD","APD",
        "CSX","TJX","NSC","KLAC","SNPS","CDNS","LRCX","MRVL","INTC","MU",
        "EMR","ROK","AME","CTVA","DD","DOW","LYB","PPG","ECL","PSX","VLO",
        "MPC","SLB","OXY","COP","EOG","DVN","HAL","WMB","TRGP","KMI","OKE",
        "F","GM","AIG","MET","PRU","AFL","ALL","TRV","PGR","CMG","YUM","DPZ",
        "DXCM","ILMN","A","ALGN","EW","IDXX","MTD","NKE","LULU","ABNB","DASH",
        "PYPL","NOW","VEEV","WDAY","CRWD","FTNT","ANET","MSCI","FICO","CPRT",
        "CTAS","ODFL","FAST","ELV","HUM","CI","CNC","MCK","CAH","UPS",
        "CEG","VST","GEV","CARR","TT","APH","VRT","ENPH","FSLR",
        "NUE","FCX","NEM","GOLD","KKR","APO","BX","ARES","TPG","BAM",
        "SPG","AMT","CCI","PSA","O","DLR","ARE","MAA","WELL","HST",
        "KVUE","SMCI","DELL","HPQ","WBD","PARA","CMCSA","DIS",
        "EL","SNA","RCL","CCL","MAR","HLT","MGM","LVS","WYNN",
        "STZ","TAP","BF-B","DEO","MNST","KDP","CLX","CHD","KEYS",
        "TER","ON","NXPI","MCHP","MPWR","SWKS","WOLF",
        "ZS","NET","DDOG","MDB","SNOW","PLTR","COIN","TEAM","OKTA","U",
        "RBLX","ROKU","SNAP","ZM","DOCU","PINS","ETSY","TTD","TWLO",
        "RIVN","LCID","LYFT","SPOT","SQ","SHOP",
    ]
    # 額外重要個股（ADR、中概股等）
    extras = [
        "BABA","PDD","JD","BIDU","NIO","XPEV","LI","TME","BILI","NTES","TCOM",
        "ZTO","WB","VIPS","TAL","EDU","FUTU","TIGR","SE","GRAB","MELI","NU",
        "TM","SONY","ASML","ARM","TSM","BHP","RIO","VALE","STLA","HMC","RACE",
        "SAP","INFY","WIT","IBN","HDB","PBR","ITUB","BBD","HSBC","UBS","DB","SAN",
        "GFL",
    ]
    tickers = set(sp500_core + extras)
    tickers.update(CN_NAMES.keys())
    tickers = [t.strip() for t in tickers if t and isinstance(t, str) and 1 <= len(t) <= 6]
    print(f"  股票宇宙: {len(tickers)} 檔")
    return sorted(tickers)


# ---- 市值快取 ----

def load_market_cap_cache():
    """從快取載入市值資料"""
    cache_path = DATA_DIR / "market_cap_cache.json"
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_dt = datetime.fromisoformat(data.get("updated", "2000-01-01"))
        if cached_dt.tzinfo is None:
            cached_dt = cached_dt.replace(tzinfo=TZ_TW)
        age = (datetime.now(TZ_TW) - cached_dt).days
        if age <= 7:
            print(f"  市值快取: {len(data.get('caps', {}))} 檔 ({age}天前)")
            return data.get("caps", {})
    return {}


def save_market_cap_cache(caps):
    """儲存市值快取"""
    cache_path = DATA_DIR / "market_cap_cache.json"
    cache_path.write_text(json.dumps({
        "updated": datetime.now(TZ_TW).isoformat(),
        "caps": caps,
    }, ensure_ascii=False), encoding="utf-8")


def fetch_market_caps(tickers, cached):
    """多線程批次取得市值"""
    from concurrent.futures import ThreadPoolExecutor
    caps = dict(cached)
    missing = [t for t in tickers if t not in caps]
    if not missing:
        return caps

    print(f"  抓取 {len(missing)} 檔市值（30 線程）...")
    sys.stdout.flush()

    def get_mc(sym):
        try:
            return sym, yf.Ticker(sym).fast_info.get("marketCap", 0)
        except Exception:
            return sym, 0

    with ThreadPoolExecutor(max_workers=30) as ex:
        results = list(ex.map(lambda s: get_mc(s), missing))

    for sym, mc in results:
        if mc and mc > 0:
            caps[sym] = mc

    print(f"  市值: {len(caps)} 檔已取得")
    save_market_cap_cache(caps)
    return caps

    print(f"  抓取 {len(missing)} 檔市值資料...")
    sys.stdout.flush()
    batch_size = 50
    done = 0
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i+batch_size]
        try:
            tickers_obj = yf.Tickers(" ".join(batch))
            for sym in batch:
                try:
                    fi = tickers_obj.tickers[sym].fast_info
                    mc = fi.get("marketCap", 0) or fi.get("market_cap", 0)
                    if mc and mc > 0:
                        caps[sym] = mc
                except Exception:
                    pass
            done += len(batch)
            sys.stdout.write(f"\r  市值: {done}/{len(missing)}")
            sys.stdout.flush()
        except Exception as e:
            done += len(batch)
    print(f"\r  市值: {len(caps)} 檔已取得          ")
    save_market_cap_cache(caps)
    return caps




# ---- 產業快取 ----

def load_industry_cache():
    """從快取載入產業資料（30天TTL）"""
    cache_path = DATA_DIR / "industry_cache.json"
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_dt = datetime.fromisoformat(data.get("updated", "2000-01-01"))
        if cached_dt.tzinfo is None:
            cached_dt = cached_dt.replace(tzinfo=TZ_TW)
        age = (datetime.now(TZ_TW) - cached_dt).days
        if age <= 30:
            print(f"  產業快取: {len(data.get('industries', {}))} 檔 ({age}天前)")
            return data.get("industries", {})
    return {}


def save_industry_cache(industries):
    """儲存產業快取"""
    cache_path = DATA_DIR / "industry_cache.json"
    cache_path.write_text(json.dumps({
        "updated": datetime.now(TZ_TW).isoformat(),
        "industries": industries,
    }, ensure_ascii=False), encoding="utf-8")


def fetch_industries(tickers, cached):
    """多線程批次取得產業分類"""
    from concurrent.futures import ThreadPoolExecutor
    industries = dict(cached)
    missing = [t for t in tickers if t not in industries]
    if not missing:
        return industries

    print(f"  抓取 {len(missing)} 檔產業分類（30 線程）...")
    sys.stdout.flush()

    def get_ind(sym):
        try:
            info = yf.Ticker(sym).info
            ind = info.get("industry", "")
            sector = info.get("sector", "")
            return sym, ind, sector
        except Exception:
            return sym, "", ""

    with ThreadPoolExecutor(max_workers=30) as ex:
        results = list(ex.map(lambda s: get_ind(s), missing))

    for sym, ind, sector in results:
        if ind:
            industries[sym] = ind

    print(f"  產業: {len(industries)} 檔已取得")
    save_industry_cache(industries)
    return industries


def get_position(sym, industries):
    """取得個股定位：手動覆蓋 > Yahoo產業翻譯 > 原文"""
    if sym in POSITION_OVERRIDE:
        return POSITION_OVERRIDE[sym]
    ind = industries.get(sym, "")
    if ind in INDUSTRY_CN:
        return INDUSTRY_CN[ind]
    return ind  # 英文原文 fallback

# ---- 價格資料 ----

def fetch_price_data(tickers):
    """批次下載近期價格"""
    print("  下載價格資料...")
    sys.stdout.flush()
    data = yf.download(tickers, period="5d", progress=False, threads=True)
    return data


def fetch_after_hours(symbols):
    """多線程取得全部個股盤後資料"""
    from concurrent.futures import ThreadPoolExecutor
    ah_data = {}

    def get_ah(sym):
        try:
            info = yf.Ticker(sym).info
            ah_chg = info.get("postMarketChangePercent") or info.get("preMarketChangePercent")
            ah_price = info.get("postMarketPrice") or info.get("preMarketPrice")
            if ah_chg is not None:
                return sym, {"ah_pct": round(ah_chg, 2), "ah_price": ah_price}
        except Exception:
            pass
        return sym, None

    print(f"  多線程抓取 {len(symbols)} 檔盤後資料（30 線程）...")
    sys.stdout.flush()
    with ThreadPoolExecutor(max_workers=30) as ex:
        results = list(ex.map(lambda s: get_ah(s), symbols))

    for sym, data in results:
        if data:
            ah_data[sym] = data

    return ah_data


# ---- 分析 ----

def analyze(tickers, price_data, market_caps, industries=None):
    """計算漲跌幅並分類"""
    close = price_data["Close"]
    if close.empty:
        return {}

    # 取最近兩個交易日
    last_two = close.tail(2)
    if len(last_two) < 2:
        return {}

    prev_close = last_two.iloc[0]
    curr_close = last_two.iloc[1]
    trade_date = last_two.index[-1].strftime("%Y-%m-%d")

    results = []
    for sym in tickers:
        if sym not in close.columns:
            continue
        pc = prev_close.get(sym)
        cc = curr_close.get(sym)
        if pd.isna(pc) or pd.isna(cc) or pc == 0:
            continue
        chg_pct = (cc - pc) / pc * 100
        mc = market_caps.get(sym, 0)
        if mc < 1e9:
            continue
        cn = CN_NAMES.get(sym, "")
        pos = get_position(sym, industries or {})
        results.append({
            "symbol": sym,
            "cn_name": cn,
            "position": pos,
            "close": round(cc, 2),
            "prev_close": round(pc, 2),
            "change_pct": round(chg_pct, 2),
            "market_cap": mc,
            "ah_pct": None,
        })

    # 分三類
    categories = {
        "mega": [],   # > $100B
        "large": [],  # $10B ~ $100B
        "mid": [],    # $1B ~ $10B
    }
    for r in results:
        mc = r["market_cap"]
        if mc >= 100e9:
            categories["mega"].append(r)
        elif mc >= 10e9:
            categories["large"].append(r)
        else:
            categories["mid"].append(r)

    # 各類按漲幅排序
    for cat in categories:
        categories[cat].sort(key=lambda x: x["change_pct"], reverse=True)

    return {"categories": categories, "trade_date": trade_date, "total": len(results)}


# ---- HTML ----

def generate_html(data, ah_data=None):
    """產生深色風格 HTML 報告，含盤中/盤後排行切換"""
    cats = data["categories"]
    trade_date = data["trade_date"]
    now_str = datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M")

    # 合併盤後資料
    if ah_data:
        for cat in cats.values():
            for stock in cat:
                if stock["symbol"] in ah_data:
                    stock["ah_pct"] = ah_data[stock["symbol"]]["ah_pct"]

    def fmt_mc(mc):
        if mc >= 1e12:
            return f"${mc/1e12:.1f}T"
        elif mc >= 1e9:
            return f"${mc/1e9:.0f}B"
        return f"${mc/1e6:.0f}M"

    def make_rows(stocks):
        rows = ""
        for i, s in enumerate(stocks, 1):
            chg = s["change_pct"]
            chg_color = "#22c55e" if chg > 0 else "#ef4444" if chg < 0 else "#94a3b8"
            chg_bg = "rgba(34,197,94,0.12)" if chg > 0 else "rgba(239,68,68,0.12)" if chg < 0 else "transparent"
            chg_sign = "+" if chg > 0 else ""

            ah = s.get("ah_pct")
            if ah is not None:
                ah_color = "#22c55e" if ah > 0 else "#ef4444" if ah < 0 else "#94a3b8"
                ah_sign = "+" if ah > 0 else ""
                ah_bg = "rgba(34,197,94,0.12)" if ah > 0 else "rgba(239,68,68,0.12)" if ah < 0 else "transparent"
                ah_html = f'<span style="color:{ah_color};font-weight:600">{ah_sign}{ah:.2f}%</span>'
            else:
                ah_html = '<span style="color:#475569">—</span>'
                ah_bg = "transparent"

            cn = s["cn_name"]
            name_html = f'<strong>{cn}</strong><br><span style="color:#64748b;font-size:0.85em">{s["symbol"]}</span>' if cn else f'<strong>{s["symbol"]}</strong>'
            pos_html = s.get("position", "") or ""
            mc_html = fmt_mc(s["market_cap"])
            close_str = f'${s["close"]:,.2f}'

            rows += f'''<tr>
<td style="text-align:center;color:#64748b">{i}</td>
<td>{name_html}</td>
<td style="color:#94a3b8;font-size:0.8em">{pos_html}</td>
<td style="text-align:right;color:#94a3b8;font-size:0.85em">{mc_html}</td>
<td style="text-align:right">{close_str}</td>
<td style="text-align:right;background:{chg_bg}"><span style="color:{chg_color};font-weight:600">{chg_sign}{chg:.2f}%</span></td>
<td style="text-align:right;background:{ah_bg}">{ah_html}</td>
</tr>\n'''
        return rows

    TH = '<thead><tr><th style="width:35px">#</th><th>名稱</th><th>定位</th><th style="width:70px;text-align:right">市值</th><th style="width:80px;text-align:right">收盤價</th><th style="width:75px;text-align:right">漲跌幅</th><th style="width:75px;text-align:right">盤後</th></tr></thead>'

    def make_table(stocks, table_id):
        return f'<table id="{table_id}">{TH}<tbody>{make_rows(stocks)}</tbody></table>'

    # 每個市值級距產生兩組排序
    cat_labels = {"mega": "千億以上", "large": "百億~千億", "mid": "10億~百億"}
    sections_html = {}
    for key in ["mega", "large", "mid"]:
        stocks = cats[key]
        up_count = sum(1 for s in stocks if s["change_pct"] > 0)
        day_table = make_table(stocks, f"day-{key}")
        ah_sorted = sorted(stocks, key=lambda x: x.get("ah_pct") if x.get("ah_pct") is not None else -999, reverse=True)
        ah_table = make_table(ah_sorted, f"ah-{key}")
        ah_count = sum(1 for s in stocks if s.get("ah_pct") is not None)

        top_day = stocks[0] if stocks else None
        top_ah = ah_sorted[0] if ah_sorted and ah_sorted[0].get("ah_pct") is not None else None

        top_day_str = f'{top_day["cn_name"] or top_day["symbol"]} {top_day["change_pct"]:+.2f}%' if top_day else ""
        top_ah_str = f'{top_ah["cn_name"] or top_ah["symbol"]} {top_ah["ah_pct"]:+.2f}%' if top_ah and top_ah.get("ah_pct") is not None else "無資料"

        active = " active" if key == "mega" else ""
        sections_html[key] = f'''<div id="cap-{key}" class="tab-content{active}">
<div class="stats">
  <div class="stat">上漲 <span class="n">{up_count}</span> / {len(stocks)} 檔</div>
  <div class="stat">盤中最強 <span class="n">{top_day_str}</span></div>
  <div class="stat">盤後最強 <span class="n">{top_ah_str}</span></div>
  <div class="stat">盤後資料 <span class="n">{ah_count}</span> 檔</div>
</div>
<div class="sort-toggle">
  <span class="sort-btn active" data-sort="day" data-cap="{key}">盤中排行</span>
  <span class="sort-btn" data-sort="ah" data-cap="{key}">盤後排行</span>
</div>
<div id="view-day-{key}">{day_table}</div>
<div id="view-ah-{key}" style="display:none">{ah_table}</div>
</div>'''

    lbr = "{"
    rbr = "}"
    css = '''* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Microsoft JhengHei','Noto Sans TC',system-ui,sans-serif; background: #0f172a; color: #e2e8f0; padding: 1em; font-size: 14px; }
h1 { font-size: 1.3em; margin-bottom: 0.3em; }
.subtitle { color: #64748b; margin-bottom: 1em; font-size: 0.85em; }
.tabs { display: flex; gap: 0; border-bottom: 2px solid #334155; margin-bottom: 1em; }
.tab { padding: 0.6em 1.2em; cursor: pointer; font-weight: 500; color: #64748b; border-bottom: 3px solid transparent; margin-bottom: -2px; font-size: 0.95em; }
.tab.active { color: #60a5fa; border-bottom-color: #3b82f6; }
.tab:hover { color: #e2e8f0; }
.tab .badge { background: #334155; color: #94a3b8; padding: 1px 6px; border-radius: 8px; font-size: 0.8em; margin-left: 4px; }
.tab.active .badge { background: #1e3a5f; color: #60a5fa; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.stats { display: flex; gap: 0.6em; margin-bottom: 0.8em; flex-wrap: wrap; }
.stat { background: #1e293b; border-radius: 6px; padding: 0.5em 0.8em; font-size: 0.85em; }
.stat .n { font-weight: 700; color: #22c55e; }
.sort-toggle { margin-bottom: 0.8em; display: flex; gap: 0; background: #1e293b; border-radius: 6px; overflow: hidden; width: fit-content; }
.sort-btn { padding: 0.4em 1.2em; cursor: pointer; font-size: 0.85em; color: #94a3b8; transition: all 0.2s; user-select: none; }
.sort-btn:hover { color: #e2e8f0; }
.sort-btn.active { background: #3b82f6; color: #fff; font-weight: 600; }
table { width: 100%; border-collapse: collapse; }
thead th { background: #1e293b; color: #94a3b8; padding: 6px 8px; text-align: left; font-weight: 500; font-size: 0.8em; position: sticky; top: 0; }
tbody td { padding: 5px 8px; border-bottom: 1px solid #1e293b; font-size: 0.85em; }
tbody tr:hover { background: #1e293b; }
.footer { margin-top: 1.5em; color: #475569; font-size: 0.75em; text-align: center; }'''

    js = '''function switchCap(name) {
  document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
  document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
  document.querySelector('[data-cap="'+name+'"]').classList.add('active');
  document.getElementById('cap-'+name).classList.add('active');
}
document.querySelectorAll('.sort-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var sort = this.getAttribute('data-sort');
    var cap = this.getAttribute('data-cap');
    this.parentElement.querySelectorAll('.sort-btn').forEach(function(b) { b.classList.remove('active'); });
    this.classList.add('active');
    document.getElementById('view-day-'+cap).style.display = sort === 'day' ? '' : 'none';
    document.getElementById('view-ah-'+cap).style.display = sort === 'ah' ? '' : 'none';
  });
});'''

    html = f'''<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>美股昨日強勢股 {trade_date}</title>
<style>{css}</style>
</head><body>
<h1>美股昨日強勢股</h1>
<p class="subtitle">交易日: {trade_date} ｜ 更新: {now_str} ｜ 每小時自動更新 (23:00~07:00) ｜ 涵蓋 {data["total"]} 檔</p>

<div class="tabs">
  <div class="tab active" data-cap="mega" onclick="switchCap('mega')">千億以上 <span class="badge">{len(cats["mega"])}</span></div>
  <div class="tab" data-cap="large" onclick="switchCap('large')">百億~千億 <span class="badge">{len(cats["large"])}</span></div>
  <div class="tab" data-cap="mid" onclick="switchCap('mid')">10億~百億 <span class="badge">{len(cats["mid"])}</span></div>
</div>

{sections_html["mega"]}
{sections_html["large"]}
{sections_html["mid"]}

<p class="footer">資料來源: Yahoo Finance ｜ 市值以美元計 ｜ 盤後資料可能延遲 ｜ 本頁不構成投資建議</p>
<script>{js}</script>
</body></html>'''
    return html


def deploy_to_github(html_path):
    """部署到 GitHub Pages 的 us.html"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("  GitHub 部署跳過")
        return False
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/us.html"
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
        "message": f"更新美股強勢股 {datetime.now(TZ_TW).strftime('%Y-%m-%d %H:%M')}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(api_url, headers=headers_gh, json=payload, timeout=30)
        r.raise_for_status()
        print(f"  GitHub Pages us.html 部署成功")
        return True
    except Exception as e:
        print(f"  GitHub 部署失敗: {e}")
        return False


# ---- Telegram ----

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

def build_tg_message(data):
    cats = data["categories"]
    lines = [f"🇺🇸 <b>美股強勢股</b> {data['trade_date']}"]
    for label, key in [("千億", "mega"), ("百億~千億", "large")]:
        top5 = cats[key][:5]
        if top5:
            lines.append(f"\n<b>【{label}】</b>")
            for s in top5:
                cn = s["cn_name"] or s["symbol"]
                lines.append(f"  {cn} <b>{s['change_pct']:+.2f}%</b> ${s['close']:,.1f}")
    return "\n".join(lines)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TG_CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=10)
        r.raise_for_status()
        print("  Telegram 推播成功")
    except Exception as e:
        print(f"  Telegram 推播失敗: {e}")


# ---- Main ----

def main():
    parser = argparse.ArgumentParser(description="美股昨日強勢股追蹤")
    parser.add_argument("--deploy", action="store_true", help="部署到 GitHub Pages")
    parser.add_argument("--tg", action="store_true", help="Telegram 推播")
    parser.add_argument("--no-ah", action="store_true", help="跳過盤後資料")
    parser.add_argument("--top-ah", type=int, default=30, help="取前N檔抓盤後資料")
    args = parser.parse_args()

    print("美股昨日強勢股追蹤")
    print("=" * 50)

    # 取得股票宇宙
    print("\n取得股票清單...")
    tickers = get_stock_universe()

    # 市值
    print("\n取得市值...")
    cached_caps = load_market_cap_cache()
    market_caps = fetch_market_caps(tickers, cached_caps)

    # 價格
    print("\n取得價格資料...")
    price_data = fetch_price_data(tickers)

    # 產業分類
    print("\n取得產業分類...")
    cached_ind = load_industry_cache()
    industries = fetch_industries(tickers, cached_ind)

    # 分析
    print("\n分析中...")
    result = analyze(tickers, price_data, market_caps, industries)
    if not result:
        print("無法取得資料")
        return

    cats = result["categories"]
    print(f"  千億以上: {len(cats['mega'])} 檔")
    print(f"  百億~千億: {len(cats['large'])} 檔")
    print(f"  10億~百億: {len(cats['mid'])} 檔")

    # 盤後資料（全部個股）
    ah_data = {}
    if not args.no_ah:
        all_syms = []
        for cat in cats.values():
            all_syms.extend([s["symbol"] for s in cat])
        if all_syms:
            print(f"\n取得盤後資料 ({len(all_syms)} 檔)...")
            ah_data = fetch_after_hours(all_syms)
            print(f"  取得 {len(ah_data)} 檔盤後資料")

    # HTML
    html = generate_html(result, ah_data)
    date_str = datetime.now(TZ_TW).strftime("%Y%m%d")
    report_path = DATA_DIR / f"us_{date_str}.html"
    report_path.write_text(html, encoding="utf-8")
    print(f"\nHTML: {report_path}")

    # 部署
    if args.deploy:
        deploy_to_github(report_path)

    # Telegram
    if args.tg:
        msg = build_tg_message(result)
        send_telegram(msg)

    # 列印前10
    print(f"\n千億以上 TOP 10:")
    for i, s in enumerate(cats["mega"][:10], 1):
        cn = s["cn_name"] or s["symbol"]
        ah = f" (盤後{s['ah_pct']:+.2f}%)" if s.get("ah_pct") is not None else ""
        print(f"  {i:2d}. {cn:<12s} {s['symbol']:<6s} ${s['close']:>8,.2f}  {s['change_pct']:+6.2f}%{ah}")


if __name__ == "__main__":
    main()
