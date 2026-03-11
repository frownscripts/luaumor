# Delta Premium Production Deploy

This setup puts the FastAPI app behind Nginx on port 80 and keeps the Python process running with systemd.

The included service file caps the app at `500M` RAM and `70%` CPU using systemd controls.

The web service no longer runs the Discord bot or periodic full-role sync by default. That work should run in a separate bot service so the website host stays stable.

## 1. Install Python packages

```bash
cd /home/container
python3 -m pip install -r requirements.txt
```

## 2. Create environment file

Create `/home/container/.env` and set at least:

```env
SITE_DOMAIN=https://deltapremium.site
APP_HOST=127.0.0.1
APP_PORT=9745
PORT=9745
DISCORD_REDIRECT_URI=https://deltapremium.site/auth/discord/callback
ENABLE_EMBEDDED_DISCORD_BOT=0
ENABLE_STARTUP_ROLE_SYNC=0
ENABLE_PERIODIC_ROLE_SYNC=0
SESSION_CLEANUP_INTERVAL_SECONDS=21600
```

Add the rest of your real secrets there instead of relying on defaults in code.

If you want Discord slash commands and background role syncing, run `bot.py` as a separate service instead of enabling it inside the website process.

## 3. Install the systemd service

```bash
sudo cp deploy/systemd/delta-premium.service /etc/systemd/system/delta-premium.service
sudo systemctl daemon-reload
sudo systemctl enable delta-premium
sudo systemctl restart delta-premium
sudo systemctl status delta-premium
```

If it fails to boot, inspect logs:

```bash
journalctl -u delta-premium -n 200 --no-pager
```

## 4. Install the Nginx site

```bash
sudo cp deploy/nginx/delta-premium.conf /etc/nginx/sites-available/delta-premium.conf
sudo ln -sf /etc/nginx/sites-available/delta-premium.conf /etc/nginx/sites-enabled/delta-premium.conf
sudo nginx -t
sudo systemctl reload nginx
```

## 4.5 Optional: run the Discord bot separately

```bash
sudo cp deploy/systemd/delta-premium-bot.service /etc/systemd/system/delta-premium-bot.service
sudo systemctl daemon-reload
sudo systemctl enable delta-premium-bot
sudo systemctl restart delta-premium-bot
sudo systemctl status delta-premium-bot
```

Set this in `/home/container/.env` for the bot if you want periodic full role checks:

```env
ROLE_SYNC_INTERVAL_MINUTES=30
```

## 5. Open the firewall

Allow HTTP and HTTPS at the server level. If you are testing the app directly before Nginx, also allow port 9745 temporarily.

Example with UFW:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

## 6. Cloudflare settings

- DNS `A` record should point to the server IP.
- Keep the record proxied only after Nginx responds on port 80.
- For a quick `521` fix, make sure the origin is reachable on port 80 first.
- For production, move Cloudflare SSL/TLS mode to `Full (strict)` after installing an origin certificate.

## 7. Verify the origin directly

Run these from the server:

```bash
curl -I http://127.0.0.1:9745
curl -I http://127.0.0.1
```

The first checks Uvicorn. The second checks Nginx.

## 8. Most likely causes of `521`

- The Python app is not running.
- Nginx is not installed or not listening on port 80.
- The firewall is blocking port 80.
- Cloudflare DNS points to the wrong IP.
- Cloudflare proxy is enabled before the origin is actually reachable.