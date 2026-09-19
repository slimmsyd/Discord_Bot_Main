# Deploy Street Oracle on Oracle Cloud (Always Free, $0 forever)

A gateway bot needs an always-on machine. Oracle Cloud's **Always Free** tier gives you a
real Linux VM that never sleeps and never charges (within free limits). Setup is ~20 minutes,
done once.

> You need only two secrets: `DISCORD_BOT_TOKEN` and `DEEPSEEK_API_KEY`.
> The bot makes only **outbound** connections (to Discord + DeepSeek), so you do **not**
> need to open any inbound firewall ports.

---

## Moving off Railway (or any expiring host) — read this first

If you are here because a trial ended, you do **not** have to go dark while you set this up.
Do these in order:

1. **Save your data while the old bot is still up.** Anything stored on Railway's disk is
   wiped when the service goes away, and it will *not* come across to the new server.
   In Discord, run:
   - `/surveyresults` — exports survey responses as a CSV
   - `/exportmembers` — exports the member roster + join data as a CSV
   Download both. (`join_log.json` cannot be exported this way, so recent join attributions
   that never made it into an export are lost — see the note below.)
2. **Build the Oracle VM and get the bot running** (Parts 1–4). Leave the old host alone
   until the new one is confirmed working, so there is no gap in uptime.
3. **Then shut the old host down.** Two copies of the bot running at once both answer
   commands, so you will briefly get doubled replies — that is expected and harmless, but
   do not leave it that way. On Railway: service → **Settings** → **Danger** → *Delete
   Service* (or just let the expired service lapse).
4. **Commands re-sync automatically** — `app.py` calls `bot.tree.sync()` on startup. Global
   commands can take up to ~1 hour to show up in Discord. To skip the wait, register an
   instant copy from your Mac while the bot is running:
   ```bash
   .venv/bin/python sync_guild.py <GUILD_ID> --commands pdfs,pdfexport,pdflink
   ```
   Run it with `--clear` later to collapse back to the single global entry.

**The good news:** on a real VM, `join_log.json` and `surveys.json` live on a disk that
persists. On Railway (no volume) **every redeploy wiped them** — so this move fixes a silent
data-loss problem rather than creating one. From here on, join attribution and survey
responses survive restarts and updates.

**Also worth knowing:** `/pdfs`, `/pdfexport`, and `/pdflink` need the bot to have
**View Channel** and **Read Message History** in every channel you want indexed — see
`SETUP_STEPS.md` §5. A channel missing either is skipped, and the reply says so.

---

## Part 1 — Create the free VM (in your browser)

1. Sign up at <https://www.oracle.com/cloud/free/> → **Start for free**.
   - A credit card is required for identity verification only. Always Free resources are
     never billed. (Do not upgrade to "Pay As You Go" unless you choose to.)
2. In the Oracle Cloud console: **☰ Menu → Compute → Instances → Create instance**.
3. Configure:
   - **Name:** `street-oracle`
   - **Image:** click *Edit* → **Canonical Ubuntu 22.04**
   - **Shape:** click *Edit* → **Ampere (Always Free eligible)** `VM.Standard.A1.Flex`
     (1 OCPU / 6 GB is plenty). If ARM shows "out of capacity," pick
     **VM.Standard.E2.1.Micro** (AMD, also Always Free) instead.
4. **SSH keys:** choose **Generate a key pair for me** → **Download private key**
   (save it, e.g. `~/Downloads/ssh-key.key`). Also download the public key.
5. Click **Create**. Wait ~1 min until state = **Running**, then copy the
   **Public IP address** shown on the instance page.

---

## Part 2 — Connect via SSH (from your Mac terminal)

```bash
# lock down the key file permissions (required by ssh)
chmod 400 ~/Downloads/ssh-key.key

# connect (replace <PUBLIC_IP> with your instance's IP)
ssh -i ~/Downloads/ssh-key.key ubuntu@<PUBLIC_IP>
```
Type `yes` if asked to trust the host. You're now on the server.

---

## Part 3 — Install and run the bot (on the server)

```bash
# system packages
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git

# get the code
git clone https://github.com/slimmsyd/Discord_Bot_Main.git
cd Discord_Bot_Main

# python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# create the secrets file
cp .env.example .env
nano .env
```
In `nano`, set your two real values, then save with **Ctrl+O, Enter, Ctrl+X**:
```
DISCORD_BOT_TOKEN=your-real-bot-token
DEEPSEEK_API_KEY=your-real-deepseek-key
```

**Quick test before making it permanent:**
```bash
python app.py
```
You should see `=== Bot Started ===` and `Slash commands synced successfully`, and the bot
goes green in Discord. Press **Ctrl+C** to stop, then set it up to run forever ⬇

---

## Part 4 — Run 24/7 with systemd (auto-start + auto-restart)

This repo ships a service file at `deploy/streetoracle.service`.

```bash
# install the service (assumes you cloned to /home/ubuntu/Discord_Bot_Main)
sudo cp ~/Discord_Bot_Main/deploy/streetoracle.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable streetoracle    # start on every boot
sudo systemctl start streetoracle     # start now

# check it's alive
sudo systemctl status streetoracle
```
The bot now runs 24/7, restarts itself if it crashes, and restarts on VM reboot.

**Live logs:**
```bash
journalctl -u streetoracle -f
```

---

## Updating the bot later
When you push new code to `main`:
```bash
ssh -i ~/Downloads/ssh-key.key ubuntu@<PUBLIC_IP>
cd Discord_Bot_Main
git pull
source venv/bin/activate && pip install -r requirements.txt   # only if deps changed
sudo systemctl restart streetoracle
```

---

## Troubleshooting
| Symptom | Fix |
|---|---|
| `systemctl status` shows `No Discord token found` | `.env` missing or wrong path — must be in `/home/ubuntu/Discord_Bot_Main/.env` |
| Bot offline / `PrivilegedIntentsRequired` | Enable **Message Content Intent** AND **Server Members Intent** in the Discord Developer Portal → Bot tab |
| `/exportmembers` exports everyone as `join_method = unknown` | Give the bot the **Manage Server** permission so it can read invites (pre-existing members can't be attributed retroactively) |
| AI replies error | `DEEPSEEK_API_KEY` wrong, or DeepSeek balance is $0 |
| ARM shape "out of host capacity" at create | Use `VM.Standard.E2.1.Micro` (AMD) or try a different Availability Domain |
| Can't SSH | Re-check `chmod 400` on the key and that you used user `ubuntu` |
| New commands don't appear in Discord | Global sync can take up to ~1 hour. Confirm what Discord actually has with `.venv/bin/python check_commands.py <GUILD_ID>`, or register an instant copy with `sync_guild.py` |
| Two bots responding / doubled replies | The old host is still running — shut it down (see "Moving off Railway") |
| `/surveyresults` empty after migrating | That data lived on the old host's disk and does not transfer. Restore from the CSV you exported before switching hosts |
