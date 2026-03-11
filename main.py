import asyncio
import os
import secrets
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import httpx
import jwt
import discord
from discord import app_commands
from discord.ext import commands
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from database import (
    add_ticket_message,
    cleanup_sessions,
    close_ticket,
    create_keys_bulk,
    create_test_keys_bulk,
    create_ticket,
    get_active_key_for_user,
    get_db,
    get_public_stats,
    get_ticket,
    get_user_by_discord_id,
    get_user_by_id,
    get_user_by_username,
    get_user_dashboard,
    hash_password,
    increment_download_count,
    increment_user_download_count,
    init_db,
    is_key_valid_for_user,
    is_session_valid,
    is_user_inactive_pending_discord,
    list_role_sync_users,
    list_tickets_admin,
    list_tickets_for_user,
    merge_user_accounts,
    redeem_key,
    set_last_login,
    store_session,
    verify_password,
)


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(value.strip())
    except (AttributeError, ValueError):
        return default


DEFAULT_SITE_DOMAIN = "https://luaumor.vercel.app"

SITE_DOMAIN = _env("SITE_DOMAIN", DEFAULT_SITE_DOMAIN).rstrip("/")
IS_VERCEL = bool(os.getenv("VERCEL"))
SITE_HOST = urlparse(SITE_DOMAIN).netloc or "luaumor.vercel.app"
SERVER_HOST = _env("SERVER_HOST", "194.164.194.118").strip()
APP_HOST = _env("APP_HOST", "0.0.0.0")
APP_PORT = _env_int("PORT", _env_int("APP_PORT", 9745))

DISCORD_CLIENT_ID = _env("DISCORD_CLIENT_ID", "1479628820448547097")
DISCORD_CLIENT_SECRET = _env("DISCORD_CLIENT_SECRET", "X8qCdJl3VCS4hhn4LpzfT3DU-gE6167C")
DISCORD_BOT_TOKEN = _env("DISCORD_BOT_TOKEN", "MTQ3OTYyODgyMDQ0ODU0NzA5Nw.GaoK_B.Myuv8k63wyXeujLO36R7EUDOXi6HuXNegFLXwU")
DISCORD_REDIRECT_URI = _env("DISCORD_REDIRECT_URI", f"{SITE_DOMAIN}/auth/discord/callback")
DISCORD_GUILD_ID = _env("DISCORD_GUILD_ID", "1443300312642359440")
ROLE_MONTHLY_ID = _env("ROLE_MONTHLY_ID", "1479633909552517162")
ROLE_LIFETIME_ID = _env("ROLE_LIFETIME_ID", "1479634137034784949")
DISCORD_ADMIN_ROLE_NAME = _env("DISCORD_ADMIN_ROLE_NAME", "++Owner")
DISCORD_GUILD_ID_INT = _to_int(DISCORD_GUILD_ID)
ENABLE_EMBEDDED_DISCORD_BOT = _env_bool("ENABLE_EMBEDDED_DISCORD_BOT", False)
ENABLE_STARTUP_ROLE_SYNC = _env_bool("ENABLE_STARTUP_ROLE_SYNC", False)
ENABLE_PERIODIC_ROLE_SYNC = _env_bool("ENABLE_PERIODIC_ROLE_SYNC", False)
RUN_BACKGROUND_WORKERS = _env_bool("RUN_BACKGROUND_WORKERS", not IS_VERCEL)
ROLE_SYNC_INTERVAL_SECONDS = max(_env_int("ROLE_SYNC_INTERVAL_SECONDS", 1800), 300)
SESSION_CLEANUP_INTERVAL_SECONDS = max(_env_int("SESSION_CLEANUP_INTERVAL_SECONDS", 21600), 300)

SELLAUTH_MONTHLY_URL = _env("SELLAUTH_MONTHLY_URL", "https://hazelshop.mysellauth.com/product/delta-premium-access")
SELLAUTH_LIFETIME_URL = _env("SELLAUTH_LIFETIME_URL", SELLAUTH_MONTHLY_URL)

JWT_SECRET = _env("JWT_SECRET", "DELTA_SUPER_SECRET_2026_CHANGE_ME")
JWT_ALGO = "HS256"
JWT_EXPIRE_MINUTES = 24 * 60

INSTALL_URL_IOS = _env(
    "INSTALL_URL_IOS",
    "itms-services://?action=download-manifest&url=https://deltaexecutor.filesadda.com/manifest%28deltav2711-premium%29.plist",
)
INSTALL_URL_IOS_DIRECT = _env(
    "INSTALL_URL_IOS_DIRECT",
    "itms-services://?action=download-manifest&url=https://delta.bz/manifest.plist",
)
INSTALL_URL_ANDROID = _env("INSTALL_URL_ANDROID", "https://cdn.gloopup.net/file/Delta-2.710.707-02.apk")
INSTALL_URL_IPA_PAGE = _env("INSTALL_URL_IPA_PAGE", "https://gloopup.net/Delta/ios/")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_used_download_jtis: set[str] = set()

ALLOWED_ORIGINS = [
    SITE_DOMAIN,
    f"https://www.{SITE_HOST.replace('www.', '')}",
    f"http://{SERVER_HOST}:{APP_PORT}",
    f"https://{SERVER_HOST}",
    f"http://localhost:{APP_PORT}",
    f"http://127.0.0.1:{APP_PORT}",
]
ALLOWED_HOSTS = [
    SITE_HOST,
    f"www.{SITE_HOST.replace('www.', '')}",
    SERVER_HOST,
    "localhost",
    "127.0.0.1",
]
if IS_VERCEL:
    ALLOWED_HOSTS.append("*.vercel.app")
RATE_LIMIT_RULES = {
    "login": (8, 300),
    "signup": (5, 3600),
    "ticket_create": (6, 300),
    "ticket_reply": (15, 300),
    "admin_update": (20, 300),
}
_rate_limit_store: defaultdict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()
discord_intents = discord.Intents.default()
discord_intents.members = True
discord_intents.message_content = True
discord_bot = commands.Bot(command_prefix="!", intents=discord_intents)
discord_bot_task: Optional[asyncio.Task] = None
session_cleanup_task: Optional[asyncio.Task] = None
role_sync_task: Optional[asyncio.Task] = None
discord_commands_synced = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with _app_lifespan(app):
        yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "img-src 'self' data: https://cdn.discordapp.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "script-src 'self' 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self' https://discord.com https://cdn.discordapp.com;"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response


def read_static_html(name: str) -> str:
    path = os.path.join(STATIC_DIR, name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def create_jwt(user_id: int, role: str) -> str:
    jti = secrets.token_hex(16)
    now = datetime.utcnow()
    exp = now + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "role": role,
        "jti": jti,
        "exp": exp,
        "iat": now,
    }
    store_session(user_id, jti, exp.isoformat())
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_jwt(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    jti = payload.get("jti")
    if not jti or not is_session_valid(jti):
        raise HTTPException(status_code=401, detail="Session expired")
    return payload


def create_discord_state(flow: str, user_id: Optional[int] = None) -> str:
    payload = {
        "flow": flow,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(minutes=10),
        "nonce": secrets.token_hex(12),
    }
    if user_id is not None:
        payload["sub"] = str(user_id)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_discord_state(state: str) -> dict:
    payload = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGO])
    if payload.get("flow") not in {"login", "link"}:
        raise ValueError("Invalid Discord state flow")
    return payload


def build_discord_oauth_url(state: str) -> str:
    return (
        "https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        "&response_type=code"
        "&scope=identify"
        f"&state={state}"
    )


def sanitize_user_row(row: dict) -> dict:
    safe = dict(row)
    safe.pop("password_hash", None)
    safe["needs_discord_link"] = is_user_inactive_pending_discord(safe)
    safe["account_active"] = not safe["needs_discord_link"]
    return safe


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_jwt(creds.credentials)
        user_id = int(payload["sub"])
        user = get_user_by_id(user_id)
        if not user or user["banned"]:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def ensure_account_access(user: dict):
    if is_user_inactive_pending_discord(user):
        raise HTTPException(
            status_code=403,
            detail="Your account is inactive until your Discord account is linked.",
        )


def _get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    client_host = request.client.host if request.client else "unknown"
    return forwarded_for or real_ip or client_host or "unknown"


async def _enforce_rate_limit(request: Request, bucket: str):
    rule = RATE_LIMIT_RULES.get(bucket)
    if not rule:
        return
    limit, window_seconds = rule
    now = datetime.utcnow().timestamp()
    key = f"{bucket}:{_get_client_identifier(request)}"
    async with _rate_limit_lock:
        attempts = _rate_limit_store[key]
        while attempts and now - attempts[0] > window_seconds:
            attempts.popleft()
        if len(attempts) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment and try again.",
            )
        attempts.append(now)


class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str
    license_key: str


class RedeemRequest(BaseModel):
    key_code: str


class GenKeysRequest(BaseModel):
    plan: str
    count: int
    note: Optional[str] = ""
    duration_seconds: Optional[int] = None


class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    banned: Optional[int] = None
    ban_reason: Optional[str] = None
    add_days: Optional[int] = None
    paused: Optional[int] = None


class UpdateKeyRequest(BaseModel):
    active: Optional[int] = None
    paused: Optional[int] = None
    add_days: Optional[int] = None
    reset: Optional[bool] = None


class PostUpdateRequest(BaseModel):
    title: str
    content: str
    category: Optional[str] = "update"
    pinned: Optional[int] = 0


class TicketCreateRequest(BaseModel):
    subject: str
    message: str
    category: Optional[str] = "support"


class TicketReplyRequest(BaseModel):
    message: str


DOWNLOAD_VARIANTS = {
    "ios_ota": INSTALL_URL_IOS,
    "ios_direct": INSTALL_URL_IOS_DIRECT,
    "android_apk": INSTALL_URL_ANDROID,
    "ios_ipa_page": INSTALL_URL_IPA_PAGE,
}


def _build_download_token(user_id: int, variant: str) -> str:
    jti = secrets.token_hex(16)
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": "download",
            "variant": variant,
            "jti": jti,
            "exp": datetime.utcnow() + timedelta(seconds=20),
            "iat": datetime.utcnow(),
        },
        JWT_SECRET,
        algorithm=JWT_ALGO,
    )


def _build_download_redirect_html(url: str, title: str, subtitle: str) -> str:
    import binascii as _bi
    import os as _os

    ub = url.encode()
    kb = _os.urandom(len(ub))
    enc = bytes(a ^ b for a, b in zip(ub, kb))
    s = _bi.hexlify(enc).decode()
    x = _bi.hexlify(kb).decode()
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>{title}</title>
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<meta name=\"referrer\" content=\"no-referrer\">
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#0a0a0f;color:#fff;font-family:system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;gap:1.25rem;padding:2rem;text-align:center}}.spinner{{width:44px;height:44px;border:3px solid rgba(99,102,241,.2);border-top-color:#6366f1;border-radius:50%;animation:spin .8s linear infinite}}@keyframes spin{{to{{transform:rotate(360deg)}}}}.title{{font-size:1.25rem;font-weight:700}}.sub{{font-size:.875rem;color:#6b7280;max-width:34rem}}.back{{margin-top:1rem;color:#6366f1;font-size:.82rem;text-decoration:none}}</style></head>
<body>
<div class=\"spinner\"></div>
<div class=\"title\">{title}</div>
<div class=\"sub\">{subtitle}</div>
<a href=\"/dashboard\" class=\"back\">Back to Dashboard</a>
<script>
(function(){{
var s='{s}',x='{x}';
var sb=new Uint8Array(s.length>>1),xb=new Uint8Array(x.length>>1);
for(var i=0;i<s.length;i+=2){{sb[i>>1]=parseInt(s.substr(i,2),16);xb[i>>1]=parseInt(x.substr(i,2),16);}}
var u=Array.from(sb,function(b,i){{return String.fromCharCode(b^xb[i]);}}).join('');
window.location.replace(u);
}})();
</script></body></html>"""


def _role_id_for_plan(plan: Optional[str]) -> Optional[str]:
    if plan == "monthly":
        return ROLE_MONTHLY_ID
    if plan == "lifetime":
        return ROLE_LIFETIME_ID
    return None


async def _discord_role_request(method: str, discord_id: str, role_id: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.request(
            method,
            f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{discord_id}/roles/{role_id}",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
        )


async def _discord_sync_roles(discord_id: str, plan: Optional[str], should_have_role: bool):
    if not discord_id or not DISCORD_GUILD_ID:
        return
    target_role = _role_id_for_plan(plan) if should_have_role else None
    for role_id in filter(None, [ROLE_MONTHLY_ID, ROLE_LIFETIME_ID]):
        if role_id != target_role:
            try:
                await _discord_role_request("DELETE", discord_id, role_id)
            except Exception:
                pass
    if target_role:
        try:
            await _discord_role_request("PUT", discord_id, target_role)
        except Exception:
            pass


async def _sync_discord_roles_for_user(user_id: int):
    user = get_user_by_id(user_id)
    if not user or not user.get("discord_id"):
        return
    active_key = get_active_key_for_user(user_id)
    await _discord_sync_roles(
        user["discord_id"],
        active_key["plan"] if active_key else None,
        bool(active_key) and not is_user_inactive_pending_discord(user),
    )


async def _sync_all_discord_roles():
    for row in list_role_sync_users():
        try:
            await _discord_sync_roles(row["discord_id"], row["plan"], row["should_have_role"])
        except Exception as exc:
            print(f"[Role sync] {exc}")


def _discord_member_is_admin(member: object) -> bool:
    if isinstance(member, discord.Member):
        if member.guild_permissions.administrator:
            return True
        return any(role.name == DISCORD_ADMIN_ROLE_NAME for role in member.roles)
    return False


async def _start_discord_bot():
    if not DISCORD_BOT_TOKEN:
        print("[Bot] Discord bot disabled: no token configured")
        return
    try:
        await discord_bot.start(DISCORD_BOT_TOKEN)
    except Exception as exc:
        print(f"[Bot] Startup error: {exc}")


@discord_bot.event
async def on_ready():
    global discord_commands_synced
    print(f"[Bot] Logged in as {discord_bot.user} ({discord_bot.user.id})")
    if not discord_commands_synced:
        try:
            if DISCORD_GUILD_ID_INT:
                guild = discord.Object(id=DISCORD_GUILD_ID_INT)
                discord_bot.tree.copy_global_to(guild=guild)
                await discord_bot.tree.sync(guild=guild)
            else:
                await discord_bot.tree.sync()
            discord_commands_synced = True
            print("[Bot] Slash commands synced")
        except Exception as exc:
            print(f"[Bot] Slash sync error: {exc}")
    if ENABLE_STARTUP_ROLE_SYNC:
        try:
            await _sync_all_discord_roles()
        except Exception as exc:
            print(f"[Bot] Initial role sync error: {exc}")


@discord_bot.tree.command(name="redeem", description="Redeem your Delta Premium license key")
@app_commands.describe(key="Your license key")
async def slash_redeem(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)
    user = get_user_by_discord_id(str(interaction.user.id))
    if not user or not user.get("password_hash"):
        await interaction.followup.send(
            f"❌ No Delta Premium account linked to your Discord. Sign up at {SITE_DOMAIN}/signup and link your Discord in the dashboard.",
            ephemeral=True,
        )
        return
    if user["banned"]:
        await interaction.followup.send("❌ Your account is banned.", ephemeral=True)
        return
    if is_user_inactive_pending_discord(user):
        await interaction.followup.send(
            "❌ Your account is inactive until Discord is linked. Finish linking from the dashboard first.",
            ephemeral=True,
        )
        return
    if get_active_key_for_user(user["id"]):
        await interaction.followup.send("✅ You already have an active premium key.", ephemeral=True)
        return

    result = redeem_key(user["id"], key.strip().upper())
    if not result["success"]:
        await interaction.followup.send(f"❌ {result['error']}", ephemeral=True)
        return

    await _sync_discord_roles_for_user(user["id"])
    plan = result.get("plan") or "premium"
    plan_label = plan.capitalize()
    expires_at = result.get("expires_at")
    expires_msg = f"\nExpires: {expires_at[:10]}" if expires_at else "\nAccess: Lifetime ♾️"
    await interaction.followup.send(
        f"🎉 **Delta Premium Activated**\nPlan: **{plan_label}**{expires_msg}\nDashboard: {SITE_DOMAIN}/dashboard",
        ephemeral=True,
    )


@discord_bot.tree.command(name="status", description="Check your Delta Premium status")
async def slash_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user = get_user_by_discord_id(str(interaction.user.id))
    if not user:
        await interaction.followup.send("❌ No account linked to your Discord.", ephemeral=True)
        return
    key = get_active_key_for_user(user["id"])
    if not key:
        await interaction.followup.send("⛔ No active premium access.", ephemeral=True)
        return
    plan = (key.get("plan") or "premium").capitalize()
    exp = key["expires_at"][:10] if key.get("expires_at") else "Never"
    extra = "\n⚠️ Account inactive until Discord is linked." if is_user_inactive_pending_discord(user) else ""
    await interaction.followup.send(f"✅ **Delta Premium Active**\nPlan: {plan}\nExpires: {exp}{extra}", ephemeral=True)


@discord_bot.tree.command(name="dm-key", description="[Admin] Send a key to a user via DM")
@app_commands.describe(user="Discord user", key="License key to send")
async def slash_dm_key(interaction: discord.Interaction, user: discord.Member, key: str):
    if not _discord_member_is_admin(interaction.user):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    try:
        await user.send(
            f"🔑 **Your Delta Premium Key**\n```{key}```\nRedeem at {SITE_DOMAIN}/dashboard or use `/redeem {key}` in Discord."
        )
        await interaction.response.send_message(f"✅ Key sent to {user.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Could not DM that user.", ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"❌ Error: {exc}", ephemeral=True)


@discord_bot.command(name="addtime")
async def cmd_addtime(ctx: commands.Context, member: discord.Member, days: int):
    if not _discord_member_is_admin(ctx.author):
        await ctx.send("❌ Admin only.")
        return
    if days <= 0:
        await ctx.send("❌ Days must be greater than 0.")
        return
    user = get_user_by_discord_id(str(member.id))
    if not user:
        await ctx.send("❌ User not found in database.")
        return
    key = get_active_key_for_user(user["id"])
    if not key:
        await ctx.send("⚠️ That user has no active key to extend.")
        return
    cur = datetime.fromisoformat(key["expires_at"]) if key.get("expires_at") else datetime.utcnow()
    new_exp = (cur + timedelta(days=days)).isoformat()
    conn = get_db()
    conn.execute("UPDATE keys SET expires_at = ? WHERE id = ?", (new_exp, key["id"]))
    conn.commit()
    conn.close()
    await _sync_discord_roles_for_user(user["id"])
    await ctx.send(f"✅ Added {days} days to {member.mention}. New expiry: {new_exp[:10]}")


@cmd_addtime.error
async def cmd_addtime_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ Usage: !addtime @user <days>")
        return
    print(f"[Bot] Command error: {error}")


def _log_background_task_result(task: asyncio.Task, task_name: str):
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        print(f"[Background] {task_name} crashed: {exc}")


def _create_background_task(coro, task_name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=task_name)
    task.add_done_callback(lambda finished_task: _log_background_task_result(finished_task, task_name))
    return task


async def _session_cleanup_loop(stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SESSION_CLEANUP_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
        try:
            cleanup_sessions()
        except Exception as exc:
            print(f"[Session cleanup] {exc}")


async def _periodic_role_sync_loop(stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=ROLE_SYNC_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
        try:
            await _sync_all_discord_roles()
        except Exception as exc:
            print(f"[Role sync loop] {exc}")


async def _run_discord_bot_forever(stop_event: asyncio.Event):
    if not DISCORD_BOT_TOKEN:
        print("[Bot] Discord bot disabled: no token configured")
        return

    while not stop_event.is_set():
        try:
            await _start_discord_bot()
        except asyncio.CancelledError:
            raise

        if stop_event.is_set():
            break

        print("[Bot] Restarting in 5 seconds")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5)
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    global discord_bot_task, session_cleanup_task, role_sync_task

    init_db()
    try:
        cleanup_sessions()
    except Exception as exc:
        print(f"[Startup] Session cleanup error: {exc}")

    print("[Delta Premium] Database initialized")
    print(f"[Delta Premium] Site domain: {SITE_DOMAIN}")
    print(f"[Delta Premium] Discord redirect: {DISCORD_REDIRECT_URI}")

    stop_event = asyncio.Event()
    app.state.stop_event = stop_event

    if ENABLE_STARTUP_ROLE_SYNC:
        try:
            await _sync_all_discord_roles()
        except Exception as exc:
            print(f"[Startup] Initial role sync error: {exc}")

    if RUN_BACKGROUND_WORKERS:
        session_cleanup_task = _create_background_task(_session_cleanup_loop(stop_event), "session_cleanup_loop")
        if ENABLE_PERIODIC_ROLE_SYNC:
            role_sync_task = _create_background_task(_periodic_role_sync_loop(stop_event), "role_sync_loop")
        if ENABLE_EMBEDDED_DISCORD_BOT:
            discord_bot_task = _create_background_task(_run_discord_bot_forever(stop_event), "discord_bot_supervisor")

    try:
        yield
    finally:
        stop_event.set()

        if discord_bot_task and not discord_bot.is_closed():
            try:
                await discord_bot.close()
            except Exception as exc:
                print(f"[Bot] Shutdown error: {exc}")

        if role_sync_task:
            role_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await role_sync_task
            role_sync_task = None

        if session_cleanup_task:
            session_cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await session_cleanup_task
            session_cleanup_task = None

        if discord_bot_task:
            try:
                await asyncio.wait_for(discord_bot_task, timeout=15)
            except Exception:
                discord_bot_task.cancel()
                with suppress(asyncio.CancelledError):
                    await discord_bot_task
            finally:
                discord_bot_task = None

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(read_static_html("index.html"))


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    return Response(status_code=204)


@app.get("/favicon.png", include_in_schema=False)
async def favicon_png():
    return Response(status_code=204)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(read_static_html("login.html"))


@app.get("/signup", response_class=HTMLResponse)
async def signup_page():
    return HTMLResponse(read_static_html("signup.html"))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return HTMLResponse(read_static_html("dashboard.html"))


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return HTMLResponse(read_static_html("admin.html"))


@app.post("/api/auth/login")
async def api_login(body: LoginRequest, request: Request):
    await _enforce_rate_limit(request, "login")
    user = get_user_by_username(body.username.strip().lower())
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user["banned"]:
        raise HTTPException(status_code=403, detail=f"Account banned: {user.get('ban_reason', '')}")
    set_last_login(user["id"])
    token = create_jwt(user["id"], user["role"])
    return {
        "token": token,
        "role": user["role"],
        "username": user["username"],
        "needs_discord_link": is_user_inactive_pending_discord(user),
    }


@app.post("/api/auth/signup")
async def api_signup(body: SignupRequest, request: Request):
    await _enforce_rate_limit(request, "signup")
    uname = body.username.strip().lower()
    if len(uname) < 3 or len(uname) > 24:
        raise HTTPException(status_code=400, detail="Username must be 3-24 chars")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 chars")
    if get_user_by_username(uname):
        raise HTTPException(status_code=400, detail="Username already taken")

    conn = get_db()
    key = conn.execute(
        "SELECT * FROM keys WHERE key_code = ? AND used = 0 AND active = 1",
        (body.license_key.strip().upper(),),
    ).fetchone()
    conn.close()
    if not key:
        raise HTTPException(status_code=400, detail="Invalid or already used license key")

    pw_hash = hash_password(body.password)
    deadline = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO users (username, password_hash, discord_link_required_by) VALUES (?, ?, ?)",
        (uname, pw_hash, deadline),
    )
    user_id = c.lastrowid
    conn.commit()
    conn.close()

    result = redeem_key(user_id, body.license_key.strip().upper())
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    user = get_user_by_id(user_id)
    token = create_jwt(user_id, user["role"])
    return {
        "token": token,
        "role": "user",
        "username": uname,
        "plan": result["plan"],
        "discord_link_required_by": deadline,
    }


@app.get("/auth/discord")
async def discord_auth(link: str = ""):
    if link == "1":
        return RedirectResponse("/login?error=link_auth_required")
    state = create_discord_state("login")
    return RedirectResponse(build_discord_oauth_url(state))


@app.get("/api/auth/discord/link-url")
async def api_discord_link_url(user=Depends(get_current_user)):
    state = create_discord_state("link", user["id"])
    return {"url": build_discord_oauth_url(state)}


@app.get("/auth/discord/callback")
async def discord_callback(code: str = None, error: str = None, state: str = ""):
    if error or not code:
        return RedirectResponse("/login?error=discord_denied")
    try:
        state_payload = decode_discord_state(state) if state else {"flow": "login"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            token_resp = await client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": DISCORD_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return RedirectResponse("/login?error=discord_token")

            user_resp = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            discord_user = user_resp.json()

        discord_id = discord_user["id"]
        discord_username = discord_user.get("global_name") or discord_user.get("username")
        discord_avatar = discord_user.get("avatar")
        if discord_avatar:
            discord_avatar = f"https://cdn.discordapp.com/avatars/{discord_id}/{discord_avatar}.png"

        if state_payload.get("flow") == "link":
            try:
                link_user_id = int(state_payload["sub"])
                link_user = get_user_by_id(link_user_id)
                if link_user and not link_user["banned"]:
                    existing = get_user_by_discord_id(discord_id)
                    if existing and existing["id"] != link_user_id:
                        if existing.get("password_hash"):
                            return RedirectResponse("/dashboard?error=discord_link_conflict")
                        merge_result = merge_user_accounts(existing["id"], link_user_id)
                        if not merge_result["success"]:
                            return RedirectResponse("/dashboard?error=discord_link_conflict")

                    conn = get_db()
                    conn.execute(
                        "UPDATE users SET discord_id=?, discord_username=?, discord_avatar=? WHERE id=?",
                        (discord_id, discord_username, discord_avatar, link_user_id),
                    )
                    conn.commit()
                    conn.close()
                    await _sync_discord_roles_for_user(link_user_id)
                    linked_user = get_user_by_id(link_user_id)
                    token = create_jwt(link_user_id, linked_user["role"])
                    return RedirectResponse(f"/dashboard#discord_token={token}&linked=1")
            except Exception as exc:
                print(f"[Link Discord] Error: {exc}")
            return RedirectResponse("/dashboard?error=link_failed")

        user = get_user_by_discord_id(discord_id)
        if not user or not user.get("password_hash"):
            return RedirectResponse("/login?error=discord_signup_required")

        conn = get_db()
        conn.execute(
            "UPDATE users SET discord_username=?, discord_avatar=? WHERE discord_id=?",
            (discord_username, discord_avatar, discord_id),
        )
        conn.commit()
        conn.close()
        user = get_user_by_id(user["id"])
        if user["banned"]:
            return RedirectResponse("/login?error=banned")

        token = create_jwt(user["id"], user["role"])
        return RedirectResponse(f"/dashboard#discord_token={token}")
    except Exception as exc:
        print(f"[Discord callback] {exc}")
        return RedirectResponse("/login?error=discord_error")


@app.get("/api/dashboard")
async def api_dashboard(user=Depends(get_current_user)):
    return get_user_dashboard(user["id"])


@app.post("/api/auth/discord/unlink")
async def api_discord_unlink(user=Depends(get_current_user)):
    if not user.get("discord_id"):
        raise HTTPException(status_code=400, detail="Discord is not linked")
    if not user.get("password_hash"):
        raise HTTPException(status_code=400, detail="This account cannot unlink Discord safely")

    conn = get_db()
    conn.execute(
        """
        UPDATE users
        SET discord_id = NULL,
            discord_username = NULL,
            discord_avatar = NULL,
            discord_link_required_by = ?
        WHERE id = ?
        """,
        ((datetime.utcnow() + timedelta(hours=24)).isoformat(), user["id"]),
    )
    conn.commit()
    conn.close()

    await _discord_sync_roles(user["discord_id"], None, False)
    return {"success": True, "message": "Discord account unlinked"}


@app.post("/api/redeem-key")
async def api_redeem_key(body: RedeemRequest, user=Depends(get_current_user)):
    if is_key_valid_for_user(user["id"]):
        raise HTTPException(status_code=400, detail="You already have an active key")
    result = redeem_key(user["id"], body.key_code.strip().upper())
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    await _sync_discord_roles_for_user(user["id"])
    return result


@app.get("/api/install")
async def api_install(user=Depends(get_current_user)):
    return await api_download_link("ios_ota", user)


@app.post("/api/downloads/{variant}")
async def api_download_link(variant: str, user=Depends(get_current_user)):
    ensure_account_access(user)
    if not is_key_valid_for_user(user["id"]):
        raise HTTPException(status_code=403, detail="No active premium access")
    if variant not in DOWNLOAD_VARIANTS or not DOWNLOAD_VARIANTS[variant]:
        raise HTTPException(status_code=404, detail="Download method not available")
    token = _build_download_token(user["id"], variant)
    return {"token": token}


@app.get("/install/go/{token}", include_in_schema=False)
async def install_resolve(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        return HTMLResponse('<h2 style="font-family:sans-serif;color:#ef4444">Invalid or expired install link. Please try again.</h2>', status_code=403)
    if payload.get("role") != "download":
        return HTMLResponse('<h2 style="font-family:sans-serif;color:#ef4444">Invalid token type.</h2>', status_code=403)
    user_id = int(payload.get("sub", 0) or 0)
    variant = payload.get("variant", "")
    jti = payload.get("jti", "")
    if jti in _used_download_jtis:
        return HTMLResponse('<h2 style="font-family:sans-serif;color:#ef4444">This install link has already been used. Return to the dashboard and click Install again.</h2>', status_code=403)
    if variant not in DOWNLOAD_VARIANTS or not DOWNLOAD_VARIANTS[variant]:
        return HTMLResponse('<h2 style="font-family:sans-serif;color:#ef4444">That download method is unavailable right now.</h2>', status_code=404)

    _used_download_jtis.add(jti)
    if len(_used_download_jtis) > 500:
        _used_download_jtis.clear()

    increment_download_count()
    increment_user_download_count(user_id)

    titles = {
        "ios_ota": ("Starting Delta Installation...", "Your iOS device should prompt you to install Delta shortly."),
        "ios_direct": ("Opening Direct Install Method 2...", "Launching the alternate signed OTA method for Delta iOS."),
        "android_apk": ("Opening Android Download...", "Preparing the Android APK download."),
        "ios_ipa_page": ("Opening Updated IPA Page...", "Taking you to the latest IPA page for manual install updates."),
    }
    title, subtitle = titles.get(variant, ("Preparing Download...", "Sending you to your secure download destination."))
    return HTMLResponse(content=_build_download_redirect_html(DOWNLOAD_VARIANTS[variant], title, subtitle))


@app.get("/api/public/stats")
async def api_public_stats():
    return get_public_stats()


@app.get("/api/updates")
async def api_updates(user=Depends(get_current_user)):
    conn = get_db()
    updates = [dict(u) for u in conn.execute("SELECT * FROM updates ORDER BY pinned DESC, created_at DESC LIMIT 50").fetchall()]
    conn.close()
    return updates


@app.get("/api/tickets")
async def api_list_tickets(user=Depends(get_current_user)):
    return list_tickets_for_user(user["id"])


@app.get("/api/tickets/{ticket_id}")
async def api_get_ticket(ticket_id: int, user=Depends(get_current_user)):
    ticket = get_ticket(ticket_id, requester_user_id=user["id"], is_admin=user["role"] == "admin")
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.post("/api/tickets")
async def api_create_ticket(body: TicketCreateRequest, request: Request, user=Depends(get_current_user)):
    await _enforce_rate_limit(request, "ticket_create")
    subject = body.subject.strip()
    message = body.message.strip()
    if not subject or not message:
        raise HTTPException(status_code=400, detail="Subject and message are required")
    if len(subject) > 120:
        raise HTTPException(status_code=400, detail="Ticket subject must be 120 characters or less")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="Ticket message must be 4000 characters or less")
    ticket = create_ticket(user["id"], subject, message, body.category or "support")
    return ticket


@app.post("/api/tickets/{ticket_id}/reply")
async def api_reply_ticket(ticket_id: int, body: TicketReplyRequest, request: Request, user=Depends(get_current_user)):
    await _enforce_rate_limit(request, "ticket_reply")
    ticket = get_ticket(ticket_id, requester_user_id=user["id"], is_admin=user["role"] == "admin")
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user["role"] != "admin" and ticket["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not allowed")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Reply message is required")
    if len(body.message.strip()) > 4000:
        raise HTTPException(status_code=400, detail="Reply message must be 4000 characters or less")
    try:
        updated = add_ticket_message(ticket_id, user["id"], "admin" if user["role"] == "admin" else "user", body.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return updated


@app.get("/api/admin/users")
async def admin_list_users(q: str = "", admin=Depends(require_admin)):
    conn = get_db()
    if q:
        rows = conn.execute(
            "SELECT * FROM users WHERE username LIKE ? OR discord_username LIKE ? ORDER BY id DESC",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    conn.close()
    return [sanitize_user_row(dict(r)) for r in rows]


@app.put("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, body: UpdateUserRequest, admin=Depends(require_admin)):
    conn = get_db()
    c = conn.cursor()
    user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    if body.username:
        exists = c.execute("SELECT id FROM users WHERE username=? AND id!=?", (body.username.lower(), user_id)).fetchone()
        if exists:
            conn.close()
            raise HTTPException(status_code=400, detail="Username taken")
        c.execute("UPDATE users SET username=? WHERE id=?", (body.username.lower(), user_id))

    if body.password:
        if len(body.password) < 6:
            conn.close()
            raise HTTPException(status_code=400, detail="Password too short")
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.password), user_id))

    if body.role is not None:
        c.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))

    if body.banned is not None:
        c.execute("UPDATE users SET banned=?, ban_reason=? WHERE id=?", (body.banned, body.ban_reason or "", user_id))

    if body.add_days and body.add_days > 0:
        key = c.execute("SELECT * FROM keys WHERE id=(SELECT active_key_id FROM users WHERE id=?)", (user_id,)).fetchone()
        if key:
            key = dict(key)
            cur_exp = datetime.utcnow() if not key["expires_at"] else datetime.fromisoformat(key["expires_at"])
            c.execute("UPDATE keys SET expires_at=? WHERE id=?", ((cur_exp + timedelta(days=body.add_days)).isoformat(), key["id"]))

    if body.paused is not None:
        key = c.execute("SELECT id FROM keys WHERE id=(SELECT active_key_id FROM users WHERE id=?)", (user_id,)).fetchone()
        if key:
            c.execute("UPDATE keys SET paused=? WHERE id=?", (body.paused, key["id"]))

    conn.commit()
    conn.close()
    await _sync_discord_roles_for_user(user_id)
    return {"success": True}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin=Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    conn = get_db()
    user_row = conn.execute("SELECT discord_id FROM users WHERE id=?", (user_id,)).fetchone()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    if user_row and user_row["discord_id"]:
        await _discord_sync_roles(user_row["discord_id"], None, False)
    return {"success": True}


@app.get("/api/admin/keys")
async def admin_list_keys(q: str = "", plan: str = "", admin=Depends(require_admin)):
    conn = get_db()
    sql = "SELECT k.*, u.username as owner FROM keys k LEFT JOIN users u ON u.id=k.used_by WHERE 1=1"
    params = []
    if q:
        sql += " AND k.key_code LIKE ?"
        params.append(f"%{q}%")
    if plan:
        sql += " AND k.plan=?"
        params.append(plan)
    sql += " ORDER BY k.id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


@app.post("/api/admin/keys/generate")
async def admin_gen_keys(body: GenKeysRequest, admin=Depends(require_admin)):
    if body.count < 1 or body.count > 200:
        raise HTTPException(status_code=400, detail="Count must be 1-200")
    if body.plan not in ("monthly", "lifetime", "test"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    if body.plan == "test":
        keys = create_test_keys_bulk(body.duration_seconds or 30, body.count, body.note or "")
    else:
        keys = create_keys_bulk(body.plan, body.count, body.note or "")
    return {"keys": keys, "count": len(keys)}


@app.put("/api/admin/keys/{key_id}")
async def admin_update_key(key_id: int, body: UpdateKeyRequest, admin=Depends(require_admin)):
    conn = get_db()
    c = conn.cursor()
    key = c.execute("SELECT * FROM keys WHERE id=?", (key_id,)).fetchone()
    if not key:
        conn.close()
        raise HTTPException(status_code=404, detail="Key not found")
    key = dict(key)
    affected_users = {r[0] for r in c.execute("SELECT id FROM users WHERE active_key_id=?", (key_id,)).fetchall()}
    if key.get("used_by"):
        affected_users.add(key["used_by"])

    if body.active is not None:
        c.execute("UPDATE keys SET active=? WHERE id=?", (body.active, key_id))
    if body.paused is not None:
        c.execute("UPDATE keys SET paused=? WHERE id=?", (body.paused, key_id))
    if body.add_days:
        cur = datetime.utcnow() if not key["expires_at"] else datetime.fromisoformat(key["expires_at"])
        c.execute("UPDATE keys SET expires_at=? WHERE id=?", ((cur + timedelta(days=body.add_days)).isoformat(), key_id))
    if body.reset:
        c.execute("UPDATE keys SET used=0, used_by=NULL, used_at=NULL, expires_at=NULL, paused=0 WHERE id=?", (key_id,))
        c.execute("UPDATE users SET active_key_id=NULL WHERE active_key_id=?", (key_id,))

    conn.commit()
    conn.close()
    for user_id in affected_users:
        await _sync_discord_roles_for_user(user_id)
    return {"success": True}


@app.delete("/api/admin/keys/{key_id}")
async def admin_delete_key(key_id: int, admin=Depends(require_admin)):
    conn = get_db()
    affected_users = [r[0] for r in conn.execute("SELECT id FROM users WHERE active_key_id=?", (key_id,)).fetchall()]
    conn.execute("UPDATE users SET active_key_id=NULL WHERE active_key_id=?", (key_id,))
    conn.execute("DELETE FROM keys WHERE id=?", (key_id,))
    conn.commit()
    conn.close()
    for user_id in affected_users:
        await _sync_discord_roles_for_user(user_id)
    return {"success": True}


@app.get("/api/admin/updates")
async def admin_list_updates(admin=Depends(require_admin)):
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM updates ORDER BY created_at DESC").fetchall()]
    conn.close()
    return rows


@app.post("/api/admin/updates")
async def admin_post_update(body: PostUpdateRequest, request: Request, admin=Depends(require_admin)):
    await _enforce_rate_limit(request, "admin_update")
    if not body.title.strip() or not body.content.strip():
        raise HTTPException(status_code=400, detail="Title and content are required")
    if len(body.title.strip()) > 120:
        raise HTTPException(status_code=400, detail="Title must be 120 characters or less")
    if len(body.content.strip()) > 6000:
        raise HTTPException(status_code=400, detail="Content must be 6000 characters or less")
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO updates (title, content, category, author, pinned) VALUES (?, ?, ?, ?, ?)",
        (body.title.strip(), body.content.strip(), body.category or "update", admin["username"], body.pinned or 0),
    )
    update_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"success": True, "id": update_id}


@app.delete("/api/admin/updates/{update_id}")
async def admin_delete_update(update_id: int, admin=Depends(require_admin)):
    conn = get_db()
    conn.execute("DELETE FROM updates WHERE id=?", (update_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/api/admin/stats")
async def admin_stats(admin=Depends(require_admin)):
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active_keys = conn.execute("SELECT COUNT(*) FROM keys WHERE used=1 AND active=1 AND paused=0").fetchone()[0]
    total_keys = conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
    unused_keys = conn.execute("SELECT COUNT(*) FROM keys WHERE used=0 AND active=1").fetchone()[0]
    total_revenue = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE status='completed'").fetchone()[0]
    open_tickets = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "active_keys": active_keys,
        "total_keys": total_keys,
        "unused_keys": unused_keys,
        "total_revenue": round(total_revenue, 2),
        "open_tickets": open_tickets,
        "download_count": get_public_stats()["download_count"],
    }


@app.get("/api/admin/tickets")
async def admin_get_tickets(admin=Depends(require_admin)):
    return list_tickets_admin()


@app.post("/api/admin/tickets/{ticket_id}/close")
async def admin_close_ticket(ticket_id: int, admin=Depends(require_admin)):
    ticket = get_ticket(ticket_id, requester_user_id=admin["id"], is_admin=True)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    updated = close_ticket(ticket_id, admin["id"])
    return updated


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=APP_HOST, port=APP_PORT, reload=False)
