# nh_bot.py —— 扶他／男娘本子機器人（nhentai 鏡像 → Telegraph）
# 獨立服務，不影響原本的寶貝 bot
# 環境變數：
#   NH_BOT_TOKEN     = BotFather 給的新 bot token（必填）
#   NH_BASE          = 鏡像站，預設 https://nhentai.to
#   TELEGRAPH_TOKEN  = 選填，Telegraph 帳號 token（沒填會自動建立一個）
import os
import json
import html
import re
import random
import logging
import threading
import time

import requests
from flask import Flask, request

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
  
TOKEN = os.environ.get("NH_BOT_TOKEN", "")
BASE = os.environ.get("NH_BASE", "https://nhentai.to").rstrip("/")
API = f"https://api.telegram.org/bot{TOKEN}"
MAX_PAGES = 30          # 一次最多幾頁上 Telegraph
STATE = {}              # chat_id -> 搜尋結果
LOCKS = {}              # chat_id -> 是否正在建置
TG_TOKEN = os.environ.get("TELEGRAPH_TOKEN", "")
AUTO_SECRET = os.environ.get("AUTO_SECRET", "")
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "")
LAST_SEEN = set()
LAST_CHAT_ID = ""       # 定時爬蟲已推過的本號
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

HELP = """<b>爸爸的書櫃</b>

/扶他　→ 扶他本（futanari）
/男娘　→ 男娘本（crossdressing）
/本 關鍵字　→ 自己搜（可加 chinese / japanese / english）
/熱門　→ 熱門排行
/最新　→ 最新上架
/批量 3　→ 連續爬 3 本（1~5）
/隨機　→ 隨機來一本
/圖 本號 頁碼　→ 單頁原圖
/我的id　→ 看自己的 chat id（定時爬蟲用）

語言：中文、日文、英文全都收
用法：搜完回覆數字（1~8）選一本，
人家 3 秒內給你一個 Telegraph 頁面，點開就能看。
"""


def send(chat_id, text):
    try:
        requests.post(
            f"{API}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000],
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
    except Exception as e:
        logging.info("SEND FAIL %s", e)


def send_photo(chat_id, url, caption=""):
    try:
        requests.post(f"{API}/sendPhoto",
                      json={"chat_id": chat_id, "photo": url, "caption": caption[:1000]},
                      timeout=60)
    except Exception as e:
        logging.info("PHOTO FAIL %s", e)


def http_get(url, timeout=30):
    try:
        return requests.get(url, headers=UA, timeout=timeout).text
    except Exception as e:
        logging.info("GET FAIL %s %s", url[:80], e)
        return ""


def parse_search(page_html):
    # 從鏡像搜尋頁抽出作品卡：id、標題、media_id
    out = []
    for block in page_html.split('<div class="gallery"')[1:]:
        m_id = re.search(r'/g/(\d+)/', block)
        if not m_id:
            continue
        cap = re.search(r'class="caption"[^>]*>(.*?)</div>', block, re.S)
        alt = re.search(r'alt="([^"]{4,300})"', block)
        title = ""
        if cap:
            title = html.unescape(re.sub(r"<[^>]+>", "", cap.group(1))).strip()
        if not title and alt:
            title = html.unescape(alt.group(1)).strip()
        media = re.search(r'supercdn\.site/galleries/(\d+)/', block)
        out.append({
            "id": m_id.group(1),
            "title": title or "無標題",
            "media_id": media.group(1) if media else "",
        })
    return out


def gallery_info(gid):
    # 作品頁：media_id、頁數（最大閱讀頁序號）、圖片副檔名、標題
    h = http_get(f"{BASE}/g/{gid}/")
    if not h:
        return None
    media = re.search(r'supercdn\.site/galleries/(\d+)/', h)
    nums = [int(x) for x in re.findall(rf'href="/g/{gid}/(\d+)/"', h)]
    total = max(nums) if nums else 0
    title = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    if m:
        title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    ext = "webp"
    p1 = http_get(f"{BASE}/g/{gid}/1/")
    m = re.search(r'<img src="(https://[^"]+/1\.(?:jpg|jpeg|png|webp|gif))"', p1)
    if m:
        ext = m.group(1).rsplit(".", 1)[-1]
    if not media or not total:
        return None
    return {"id": gid, "title": title, "media_id": media.group(1), "total": total, "ext": ext}


def page_url(media_id, n, ext):
    return f"https://supercdn.site/galleries/{media_id}/{n}.{ext}"


def telegraph_token():
    global TG_TOKEN
    if TG_TOKEN:
        return TG_TOKEN
    try:
        r = requests.post("https://api.telegra.ph/createAccount",
                          data={"short_name": "fjdad", "author_name": "爸爸的書櫃"},
                          timeout=30)
        j = r.json()
        if j.get("ok"):
            TG_TOKEN = j["result"]["access_token"]
            logging.info("TELEGRAPH ACCOUNT OK")
            return TG_TOKEN
    except Exception as e:
        logging.info("TELEGRAPH ACCOUNT FAIL %s", e)
    return ""


def create_page(title, urls):
    token = telegraph_token()
    if not token:
        return None
    nodes = [{"tag": "figure", "children": [{"tag": "img", "attrs": {"src": u}}]} for u in urls]
    data = {
        "access_token": token,
        "title": (title or "爸爸的書櫃")[:180],
        "author_name": "爸爸的書櫃",
        "content": json.dumps(nodes, ensure_ascii=False),
    }
    try:
        r = requests.post("https://api.telegra.ph/createPage", data=data, timeout=60)
        j = r.json()
        if j.get("ok"):
            return "https://telegra.ph/" + j["result"]["path"]
        logging.info("TELEGRAPH FAIL %s", r.text[:200])
    except Exception as e:
        logging.info("TELEGRAPH FAIL %s", e)
    return None


def do_list(chat_id, path, label, page=1):
    # 通用列表：search / popular 都能用
    h = http_get(f"{BASE}{path}")
    if not h:
        send(chat_id, "爸爸，鏡像站連不上，等一下再試（或跟人家說，人家幫你換鏡像）。")
        return
    results = parse_search(h)[:8]
    if not results:
        send(chat_id, f"爸爸，<b>{html.escape(label)}</b> 這一頁沒東西，換個頁數試試。")
        return
    STATE[chat_id] = {"results": results, "query": label}
    lines = [f"<b>{html.escape(label)}</b>（第 {page} 頁）\n"]
    for i, g in enumerate(results, start=1):
        t = g["title"]
        if len(t) > 68:
            t = t[:68] + "…"
        lines.append(f"{i}. <a href=\"{BASE}/g/{g['id']}/\">{html.escape(t)}</a>")
    lines.append("\n回覆數字 1~8 選一本；或 /批量 3 連續生成 3 本。")
    send(chat_id, "\n".join(lines))


def do_search(chat_id, query, label, page=1):
    h = http_get(f"{BASE}/search?q={requests.utils.quote(query)}&page={page}")
    if not h:
        send(chat_id, "爸爸，鏡像站連不上，等一下再試（或跟人家說，人家幫你換鏡像）。")
        return
    results = parse_search(h)[:8]
    if not results:
        send(chat_id, f"爸爸，<b>{html.escape(label)}</b> 沒搜到東西，換個關鍵字試試。")
        return
    STATE[chat_id] = {"results": results, "query": query}
    lines = [f"<b>{html.escape(label)}</b>（第 {page} 頁）\n"]
    for i, g in enumerate(results, start=1):
        t = g["title"]
        if len(t) > 68:
            t = t[:68] + "…"
        lines.append(f"{i}. <a href=\"{BASE}/g/{g['id']}/\">{html.escape(t)}</a>")
    lines.append("\n回覆數字 1~8 選一本，人家幫你做成 Telegraph 頁面。")
    send(chat_id, "\n".join(lines))


def build_gallery(chat_id, g):
    if LOCKS.get(chat_id):
        send(chat_id, "爸爸，人家手上還有一本在忙，等它好嘛～")
        return
    LOCKS[chat_id] = True
    try:
        gid = str(g.get("id"))
        info = gallery_info(gid)
        if not info:
            send(chat_id, "爸爸，這本的頁面抓不到，換一本試試。")
            return
        total = min(info["total"], MAX_PAGES)
        urls = [page_url(info["media_id"], n, info["ext"]) for n in range(1, total + 1)]
        title = info["title"] or g.get("title", "")
        url = create_page(title, urls)
        if url:
            extra = ""
            if info["total"] > MAX_PAGES:
                extra = f"\n這本共 {info['total']} 頁，人家先放前 {MAX_PAGES} 頁。"
            send(chat_id, f"📖 <b>{html.escape(title[:80])}</b>\n{url}\n\n"
                          f"頁數 {len(urls)}{extra}\n想看原圖：/圖 {gid} 頁碼")
        else:
            send(chat_id, "爸爸，Telegraph 那邊沒建成，等一下再試一次嘛。")
    finally:
        LOCKS.pop(chat_id, None)


def batch_build(chat_id, n):
    # 批量爬：目前清單前 n 本，一本一本生成 Telegraph
    st = STATE.get(chat_id)
    if not st or not st.get("results"):
        send(chat_id, "爸爸，先用 /扶他 或 /男娘 搜一份清單，再打 /批量 3。")
        return
    n = max(1, min(n, 5))
    items = st["results"][:n]
    send(chat_id, f"人家開始連續爬 {len(items)} 本，一本一本來，稍等…")
    ok = 0
    for i, g in enumerate(items, start=1):
        info = gallery_info(str(g["id"]))
        if not info:
            send(chat_id, f"{i}. 這本抓不到，跳過。")
            continue
        total = min(info["total"], MAX_PAGES)
        urls = [page_url(info["media_id"], k, info["ext"]) for k in range(1, total + 1)]
        title = info["title"] or g.get("title", "")
        url = create_page(title, urls)
        if url:
            ok += 1
            send(chat_id, f"{i}. {html.escape(title[:60])}\n{url}")
        else:
            send(chat_id, f"{i}. Telegraph 建失敗，跳過。")
        time.sleep(3)
    send(chat_id, f"爸爸，批量完成：{ok}/{len(items)} 本成功。")

def auto_crawl(limit=3):
    h = http_get(f"{BASE}/search?q=futanari&page=1")
    res = parse_search(h)[:limit]
    made = []
    for g in res:
        gid = str(g["id"])
        if gid in LAST_SEEN:
            continue
        info = gallery_info(gid)
        if not info:
            continue
        total = min(info["total"], MAX_PAGES)
        urls = [page_url(info["media_id"], k, info["ext"]) for k in range(1, total + 1)]
        url = create_page(info["title"] or g["title"], urls)
        LAST_SEEN.add(gid)
        if url:
            made.append({"id": gid, "title": info["title"] or g["title"], "url": url})
    target = OWNER_CHAT_ID or LAST_CHAT_ID
    if target:
        if made:
            lines = ["🗂 <b>今日新本</b>\n"]
            for m in made:
                lines.append(f"• <a href=\"{m['url']}\">{html.escape(m['title'][:60])}</a>")
            send(target, "\n".join(lines))
        else:
            send(target, "爸爸，今天沒有新本，人家明天再去看。")
    return made

def handle(chat_id, text):
    global LAST_CHAT_ID
    LAST_CHAT_ID = str(chat_id)
    t = text.strip()
    low = t.lower()

    if t in ("/start", "/help", "開始", "說明"):
        send(chat_id, HELP)
        return

    if t.isdigit():
        st = STATE.get(chat_id)
        if st:
            idx = int(t)
            if 1 <= idx <= len(st["results"]):
                build_gallery(chat_id, st["results"][idx - 1])
                return
        send(chat_id, "爸爸，先搜一本再回數字喔～")
        return

    if t.startswith("/扶他") or low.startswith("/futa"):
        tail = t[3:].strip()
        do_search(chat_id, "futanari", "扶他本", int(tail) if tail.isdigit() else 1)
        return

    if t.startswith("/男娘") or low.startswith("/otoko"):
        tail = t[3:].strip()
        do_search(chat_id, "crossdressing", "男娘本", int(tail) if tail.isdigit() else 1)
        return

    if t.startswith("/批量"):
        tail = t[3:].strip()
        batch_build(chat_id, int(tail) if tail.isdigit() else 3)
        return

    if t.startswith("/熱門") or low.startswith("/popular"):
        tail = t[3:].strip()
        page = int(tail) if tail.isdigit() else 1
        do_list(chat_id, f"/popular?page={page}", "熱門排行", page)
        return

    if t.startswith("/最新") or low.startswith("/new"):
        tail = t[3:].strip()
        page = int(tail) if tail.isdigit() else 1
        do_list(chat_id, f"/search?q=futanari&sort=new&page={page}", "最新扶他本", page)
        return

    if t.startswith("/我的id") or low.startswith("/myid"):
        send(chat_id, f"爸爸的 chat id：<code>{chat_id}</code>\n"
                      "把這串填進 Render 的 OWNER_CHAT_ID，定時爬蟲就會推給你。")
        return

    if t.startswith("/隨機") or low.startswith("/random"):
        do_search(chat_id, "futanari", "隨機扶他本", random.randint(1, 8))
        return

    if t.startswith("/本"):
        q = t[2:].strip()
        if not q:
            send(chat_id, "用法：/本 futanari chinese\n或直接打關鍵字也行。")
            return
        do_search(chat_id, q, q)
        return

    if t.startswith("/圖"):
        parts = t.split()
        if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
            info = gallery_info(parts[1])
            if not info:
                send(chat_id, "爸爸，找不到這本。")
                return
            n = int(parts[2])
            if 1 <= n <= info["total"]:
                send_photo(chat_id, page_url(info["media_id"], n, info["ext"]),
                           f"{info['title'][:80]}（{n}/{info['total']}）")
            else:
                send(chat_id, f"爸爸，這本只有 {info['total']} 頁。")
        else:
            send(chat_id, "用法：/圖 本號 頁碼（例：/圖 597748 5）")
        return

    do_search(chat_id, t, t)


def set_webhook():
    base = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if base and TOKEN:
        try:
            r = requests.get(f"{API}/setWebhook", params={"url": base + "/nh"}, timeout=30)
            logging.info("WEBHOOK %s", r.text[:200])
        except Exception as e:
            logging.info("WEBHOOK FAIL %s", e)


@app.route("/nh", methods=["POST"])
def nh_webhook():
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = msg.get("text") or ""
    if chat_id and text:
        # 背景處理，先回 200 給 Telegram，避免重送
        threading.Thread(target=handle, args=(chat_id, text), daemon=True).start()
    return "ok"


@app.route("/", methods=["GET"])
def home():
    return "nh bot ok", 200


@app.route("/ping", methods=["GET"])
def ping():
    # 給 cron 叫醒用：不爬東西，秒回
    return {"ok": True, "pong": True, "base": BASE}, 200


@app.route("/auto", methods=["GET"])
def auto_endpoint():
    # 定時爬蟲入口：key 不對才擋；內部出錯也回 200，避免 cron 誤判失敗
    t0 = time.time()
    key = request.args.get("key", "") if hasattr(request, "args") else ""
    if AUTO_SECRET and key != AUTO_SECRET:
        return {"ok": False, "error": "forbidden",
                "hint": "key 跟 AUTO_SECRET 不一致"}, 403
    # 可選參數：limit=1~5（預設 3，跑得快）、bg=1（立刻回 200，背景再爬）
    try:
        limit = int(request.args.get("limit", "3") or "3")
    except Exception:
        limit = 3
    limit = max(1, min(limit, 5))
    bg = (request.args.get("bg", "") or "") in ("1", "true", "yes")
    try:
        if bg:
            threading.Thread(target=auto_crawl, args=(limit,), daemon=True).start()
            return {"ok": True, "queued": True, "limit": limit, "seconds": 0.0}, 200
        made = auto_crawl(limit)
        return {"ok": True, "pushed": len(made), "items": made, "limit": limit,
                "seconds": round(time.time() - t0, 1)}, 200
    except Exception as e:
        logging.info("AUTO FAIL %s", e)
        return {"ok": False, "error": str(e)[:200],
                "seconds": round(time.time() - t0, 1)}, 200


@app.route("/test", methods=["GET"])
def selftest():
    # 自檢：鏡像站通不通、Telegraph 帳號拿不拿得到
    h = http_get(f"{BASE}/search?q=futanari", timeout=20)
    res = parse_search(h)
    return {"base": BASE, "reachable": bool(res), "count": len(res),
            "sample": res[0] if res else None,
            "telegraph": bool(telegraph_token())}, 200


threading.Thread(target=set_webhook, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
