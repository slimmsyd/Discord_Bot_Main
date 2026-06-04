# Street Oracle — Setup & Deploy (DeepSeek edition)

The bot is now **Discord + DeepSeek only**. No OpenAI, no MongoDB, no Twitter, no Azure.
You need exactly **two secrets**: a Discord bot token and a DeepSeek API key.

Commands the bot ships with (all powered by DeepSeek):
`/dearoracle` · `/summarize` · `/sumvideo` · `/detailvideo` · `/finnasumthisup` · `/fryemup`

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
   **MESSAGE CONTENT INTENT** → **Save Changes**.
   (The bot reads channel messages for `/summarize` and `/fryemup`; it will crash-loop on
   login without this.)

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
