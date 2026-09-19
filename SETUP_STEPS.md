# Street Oracle — Setup & Deploy (DeepSeek edition)

The bot is now **Discord + DeepSeek only**. No OpenAI, no MongoDB, no Twitter, no Azure.
You need exactly **two secrets**: a Discord bot token and a DeepSeek API key.

Commands the bot ships with (all powered by DeepSeek):
`/dearoracle` · `/summarize` · `/sumvideo` · `/detailvideo` · `/finnasumthisup` · `/fryemup` · `/listchannel` · `/pdfs` · `/pdfexport` · `/pdflink`

Owner/admin-only:
`/exportmembers` — exports every member (ID, tag, account-created + join dates, roles, booster
status, and how they joined) as a private CSV. See **§4** for its extra requirements.

---

## 1. Get your two API keys

### A) DeepSeek API key (NEW — this replaces OpenAI)
1. Go to <https://platform.deepseek.com>
2. Sign in → **API Keys** (left sidebar) → **Create new API key**.
3. Copy it (starts with `sk-...`). You only see it once.
4. Add credit: **Top up / Billing** — DeepSeek is pay-as-you-go and very cheap, but the
   key won't work with a $0 balance. Add a few dollars.
5. This is your `DEEPSEEK_API_KEY`.

### B) Discord bot token (ROTATE — the old one leaked on GitHub)
1. Go to <https://discord.com/developers/applications> → open **Street Oracle**.
2. **Bot** tab → **Reset Token** → **Yes, do it** → **Copy**.
3. This is your `DISCORD_BOT_TOKEN`.
4. Same page, scroll to **Privileged Gateway Intents** → turn ON
   **MESSAGE CONTENT INTENT** *and* **SERVER MEMBERS INTENT** → **Save Changes**.
   (Message Content powers `/summarize` and `/fryemup`; Server Members powers
   `/exportmembers` and join tracking. The bot **crash-loops on login** —
   `PrivilegedIntentsRequired` — if Server Members is off after this update.)

> You do NOT need to re-invite the bot — it's already in your server.

### Put them in your local `.env` (for testing on your machine)
Open `.env` in this folder and set:
```
DISCORD_BOT_TOKEN=the-token-you-just-reset
DEEPSEEK_API_KEY=sk-your-deepseek-key
```
(`.env` is gitignored — it will never be committed. `.env.example` shows the format.)

---

## 2. Deploy to Railway

### One-time: push the cleaned code to GitHub
```bash
cd ~/Desktop/Discord_Bot_Main
git push origin main
```

### Create the Railway service
1. Go to <https://railway.app> → sign in with GitHub.
2. **New Project → Deploy from GitHub repo → `slimmsyd/Discord_Bot_Main`**.
3. Railway detects the `Dockerfile` and builds it automatically.
   (The Dockerfile runs `python app.py`, which starts the bot correctly.)
4. Open the service → **Variables** tab → **+ New Variable**, add both:
   - `DISCORD_BOT_TOKEN` = your reset token
   - `DEEPSEEK_API_KEY`  = your DeepSeek key
5. Railway redeploys on save. You don't need a public domain or port — it's a worker bot.

### Confirm it deployed
- Open the **Deploy Logs**. You should see:
  ```
  === Bot Started ===
  Name: Street Oracle ...
  Slash commands synced successfully
  ```
- If you see `No Discord token found` or `No DeepSeek API key found`, a variable is missing/misspelled — fix it in the Variables tab.
- From now on, every `git push origin main` auto-redeploys.

---

## 3. Test it in a Discord channel

1. In Discord, check the bot's status dot is **green** (online) in your server member list.
2. Go to any channel the bot can see (e.g. `#general`).
3. Type `/` — you should see Street Oracle's commands pop up. (If they don't appear, wait ~1
   minute for command sync, or fully restart your Discord client.)
4. Run a quick test of each path:
   - `/dearoracle question: what is stoicism?` → should reply starting with "Young God,"
   - `/summarize` → summarizes the last 20 messages in the channel
   - `/fryemup` → roasts based on recent messages
   - `/finnasumthisup url: <any article link>` → street-style article breakdown
   - `/sumvideo url: <youtube link>` → video summary
5. If a command shows **"The application did not respond"**, the bot process isn't running —
   check Railway is deployed and the logs show "Bot Started".

---

## 4. `/exportmembers` (owner/admin only) — extra requirements

This command dumps the full member roster + join data to a private CSV. It needs three things
beyond the normal setup:

1. **Server Members Intent** ON in the Developer Portal (see §1B). Without it the bot won't even
   start after this update.
2. **Bot permission "Manage Server"** in your Discord server. The bot reads the server's invite
   list to attribute joins; without this permission, join tracking is silently skipped (members
   still export, but every `join_method` shows `unknown`).
3. **`OWNER_IDS`** (optional) — set this env var to your Discord user ID(s), comma/space
   separated, to allow specific owners regardless of server roles. Anyone with the server's
   **Administrator** permission can run it without this.

**About "how they joined":** Discord does not reveal, after the fact, which invite an existing
member used — so everyone already in the server exports as `join_method = unknown`. From the
moment this deploys, every *new* join is attributed to its invite + inviter automatically (stored
in `join_log.json`, which is gitignored).

The CSV is sent **ephemerally** (only the admin who ran the command sees it).

---

## 5. `/pdfs` · `/pdfexport` · `/pdflink` — extra requirements

These three commands index the PDFs members have uploaded. They **never download
or open the files** — they read attachment metadata (name, size, uploader, date)
and hand out download links on demand.

**Permissions the bot needs, in every channel you want indexed:**

- **View Channel**
- **Read Message History**

A channel missing either is skipped, and the reply lists which ones were skipped —
so a short index usually means a permission is missing, not that the channel is
empty.

**How to grant them:** Server Settings → Roles → the bot's role → enable
**View Channels** and **Read Message History** for the categories and channels
you want covered. Channel-level permissions override role-level ones, so check
the individual channels too.

**Nothing else to configure:** no new secrets, no new environment variables, and
no database. The index is rebuilt from Discord every time you run a command (a
10-minute in-memory cache makes repeat runs instant), so it survives redeploys and
can never go stale.

**Notes**

- Members only ever see channels they can already view. They cannot use these
  commands to discover a locked channel or pull a file out of one.
- Download links Discord gives us expire after about 24 hours. `/pdflink`
  re-fetches the original message to mint a fresh link, so retrieval still works
  months later — as long as the message has not been deleted.
- `/pdfs` with no channel or category scans the whole server and warns up front
  that it may take a while.
- The CSV/JSON from `/pdfexport` includes stable IDs (`channel_id`,
  `message_id`, `attachment_id`) so the community site can build working links
  later.

---

## Local testing (optional, before deploying)
```bash
cd ~/Desktop/Discord_Bot_Main
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```
Watch the terminal for the "Bot Started" banner, then test commands in Discord. `Ctrl+C` to stop.

---

## Troubleshooting
| Symptom | Fix |
|---|---|
| "The application did not respond" | Bot not running → check Railway deploy/logs |
| Bot offline (grey dot) | Wrong/expired `DISCORD_BOT_TOKEN`, or MESSAGE CONTENT INTENT off |
| Commands don't appear after `/` | Wait for sync (~1 min) or restart Discord client |
| AI replies error out | `DEEPSEEK_API_KEY` wrong, or DeepSeek balance is $0 |
| Crash on startup: "No DeepSeek API key found" | Add `DEEPSEEK_API_KEY` to Railway Variables |
| Crash on startup: `PrivilegedIntentsRequired` | Turn ON **SERVER MEMBERS INTENT** (and Message Content) in the Developer Portal → Bot tab |
| `/exportmembers` says "restricted" | You lack server Administrator and aren't in `OWNER_IDS` |
| Export shows everyone as `join_method = unknown` | Bot lacks **Manage Server** perm, or these members joined before tracking started |
