# 書櫃機器人（nh_bot.py）

nhentai 爬蟲 Bot：搜扶他（futanari）／男娘（crossdressing）本，
自動整理成 Telegraph 頁面，點開就能看。中文／日文／英文全收。

---

## 一、架構

```
Telegram 使用者
  → Telegram Bot API（webhook: /nh）
  → Render 上的 Flask（nh_bot.py）
  → 爬 nhentai.to 鏡像站 HTML（搜尋頁 → 作品頁）
  → 抽出圖片 CDN 網址（supercdn.site）
  → 建立 Telegraph 頁面
  → 回傳 telegra.ph 連結給使用者
```

**為什麼走鏡像站**：nhentai 官網對機房 IP 直接 403（Render 也是機房 IP），
鏡像站 `nhentai.to` 實測搜尋、作品頁、圖片 CDN 全部可用。

---

## 二、檔案清單（只留爬蟲要這些）

| 檔案 | 用途 |
|---|---|
| `nh_bot.py` | 機器人本體（爬蟲 + Telegram + Telegraph） |
| `requirements.txt` | flask、requests、gunicorn 等依賴 |
| `Procfile` | 啟動指令（Render 用 Start Command 覆蓋也可以） |

> 其他檔案（app.py、chat.html、人格設定.md、colab notebook）是別的專案，用不到可以刪。

---

## 三、環境變數

在 Render → 你的服務 → **Environment** 設定：

```
NH_BOT_TOKEN   = BotFather 給的 bot token（必填）
NH_BASE        = https://nhentai.to（選填，鏡像站換域名時改這）
AUTO_SECRET    = 自己訂一串（定時爬蟲用，防止別人亂戳）
OWNER_CHAT_ID  = 你的 chat id（定時爬蟲推播對象）
TELEGRAPH_TOKEN= 選填，不填會自動建立一個 Telegraph 帳號
```

---

## 四、部署步驟

1. Telegram 找 `@BotFather` → `/newbot` → 取名字 → 拿到 token
2. Render → **New → Web Service** → 選這個 GitHub 倉庫
3. **Start Command** 填：
   ```
   gunicorn nh_bot:app --timeout 600
   ```
4. Environment 加上 `NH_BOT_TOKEN`
5. 部署完成後 webhook 會自動設定（讀 Render 的 `RENDER_EXTERNAL_URL`）
6. 對 bot 打 `/start` 測試

---

## 五、指令

```
/扶他          → 扶他本（futanari），可加頁數 /扶他 2
/男娘          → 男娘本（crossdressing）
/本 關鍵字      → 自己搜，可加 chinese / japanese / english
/熱門          → 熱門排行
/最新          → 最新上架
/批量 3        → 連續爬 3 本（1~5）
/隨機          → 隨機扶他本
/圖 本號 頁碼   → 單頁原圖
/我的id        → 查自己的 chat id
回數字 1~8     → 選一本，生成 Telegraph 頁
```

---

## 六、爬蟲模式（A＋C＋D）

### A：批量爬

```
/扶他        → 拿到 8 本清單
/批量 3      → 連續爬前 3 本，一本一個 Telegraph 連結
```

上限 5 本（Telegraph 有建頁頻率限制，太多會被擋）。

### C：排行／最新

```
/熱門   → /popular 頁面
/最新   → /search?q=futanari&sort=new
```

> 鏡像站沒有獨立「最新列表頁」，首頁不給作品卡，
> 所以「最新」是用搜尋的 sort=new 參數逼近，排序不一定 100% 照時間。

### D：定時爬（無人值守）

1. 對 bot 打 `/我的id`，記下那串數字
2. Render 加環境變數：
   ```
   AUTO_SECRET   = 自己訂一串
   OWNER_CHAT_ID = 那串數字
   ```
3. `cron-job.org` 免費註冊 → 建排程：
   ```
   URL：https://你的服務.onrender.com/auto?key=你的AUTO_SECRET
   頻率：每天一次
   ```
4. 之後每天自動爬第一頁、只推沒推過的新本，不重複洗版

> Render 重啟後記憶會清空，第一次會重推一批，第二次就恢復正常。

---

## 七、自檢與排錯

```
瀏覽器打開 /test
→ 回 {"reachable": true, ...} 代表鏡像站連得到
→ reachable: false 就換鏡像（改 NH_BASE）
```

常見狀況：

| 症狀 | 原因 | 解法 |
|---|---|---|
| 打指令沒反應 | webhook 沒設好 | 重啟服務，或手動開 `https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://你的服務.onrender.com/nh` |
| 搜尋回「連不上」 | 鏡像站掛了／擋 IP | 改 `NH_BASE` 換鏡像 |
| Telegraph 建頁失敗 | 帳號 token 失效 | 重啟服務（會自動重建帳號） |
| 圖片空白 | CDN 換域名 | 改程式裡的 `supercdn.site` 或改 `NH_BASE` |

---

## 八、限制

- 一本最多放 30 頁（超過會提示，可分次看）
- 圖片直接熱連結鏡像 CDN，不佔 Render 空間、不耗流量
- Render 免費版 512MB 記憶體、無排程 → 定時靠 cron-job.org 外部觸發
- 鏡像站是第三方，隨時可能換域名或關站，改 `NH_BASE` 即可應對
