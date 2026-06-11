# Deploy Street Oracle on Oracle Cloud (Always Free, $0 forever)

A gateway bot needs an always-on machine. Oracle Cloud's **Always Free** tier gives you a
real Linux VM that never sleeps and never charges (within free limits). Setup is ~20 minutes,
done once.

> You need only two secrets: `DISCORD_BOT_TOKEN` and `DEEPSEEK_API_KEY`.
> The bot makes only **outbound** connections (to Discord + DeepSeek), so you do **not**
> need to open any inbound firewall ports.

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
