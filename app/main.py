import asyncio
import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
CLI_DIR = BASE_DIR.parent / "cli"
STATIC_DIR = BASE_DIR / "static"

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data" if os.path.exists("/app") else str(BASE_DIR.parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "cred_manager.db"

INVITE_CODE = os.environ.get("INVITE_CODE", "snh48ssr")
ADMIN_INIT_PASSWORD = os.environ.get("ADMIN_INIT_PASSWORD", "Admin@snh48ssr2026")
GITHUB_REPO_URL = os.environ.get("GITHUB_REPO_URL", "https://github.com/NannaOlympicBroadcast/blitz-cred-manager")
GITHUB_RAW_BASE = os.environ.get(
    "GITHUB_RAW_BASE", "https://raw.githubusercontent.com/NannaOlympicBroadcast/blitz-cred-manager/main"
)

SECRET_KEY_FILE = DATA_DIR / "jwt_secret.key"
if os.environ.get("JWT_SECRET"):
    JWT_SECRET = os.environ["JWT_SECRET"]
elif SECRET_KEY_FILE.exists():
    JWT_SECRET = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
else:
    JWT_SECRET = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(JWT_SECRET, encoding="utf-8")


# ---------------------------------------------------------------------------
# Database Layer (SQLite)
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt: Optional[str] = None) -> str:
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if "$" not in stored_hash:
        return False
    salt, _ = stored_hash.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored_hash)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            mcp_key TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS env_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            description TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS file_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            content BLOB NOT NULL,
            size INTEGER NOT NULL,
            description TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, filename)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS temp_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            scope TEXT DEFAULT 'user',
            expires_at REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # Initialize admin account if not present
    cur.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cur.fetchone():
        mcp_key = "mcp_" + secrets.token_hex(16)
        cur.execute(
            "INSERT INTO users (username, password_hash, is_admin, mcp_key, created_at) VALUES (?, ?, 1, ?, ?)",
            ("admin", hash_password(ADMIN_INIT_PASSWORD), mcp_key, now_iso()),
        )
        print(f"[INIT] Created initial admin user 'admin' (mcp_key={mcp_key})")
    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------------------------
# JWT / Session Helpers
# ---------------------------------------------------------------------------
def create_session_token(user_id: int, username: str, is_admin: bool) -> str:
    payload = {
        "uid": user_id,
        "sub": username,
        "adm": is_admin,
        "exp": int(time.time()) + 86400 * 7,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    sig = hmac.new(JWT_SECRET.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_session_token(token: str) -> Dict[str, Any]:
    if "." not in token:
        raise HTTPException(status_code=401, detail="Invalid session token")
    raw, sig = token.split(".", 1)
    expected = hmac.new(JWT_SECRET.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Invalid token signature")
    pad = "=" * (-len(raw) % 4)
    payload = json.loads(base64.urlsafe_b64decode(raw + pad).decode("utf-8"))
    if payload.get("exp", 0) < time.time():
        raise HTTPException(status_code=401, detail="Session token expired")
    return payload


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> sqlite3.Row:
    raw_token = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization[7:].strip()
    elif token:
        raw_token = token.strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    payload = verify_session_token(raw_token)
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (payload["uid"],)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="User not found")
    return row


def get_public_base_url(request: Request) -> str:
    env_public = os.environ.get("PUBLIC_SERVER_URL", "").strip()
    if env_public:
        return env_public.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    # On blitz.cloud, external traffic is always https
    if host and "blitz.cloud" in host:
        proto = "https"
    return f"{proto}://{host}".rstrip("/")


def generate_temp_token(user_id: int, server_url: str, ttl_minutes: int = 15, scope: str = "user") -> Dict[str, Any]:
    ttl_minutes = max(1, min(ttl_minutes, 1440))
    encoded_url = base64.urlsafe_b64encode(server_url.encode("utf-8")).decode("utf-8").rstrip("=")
    secret_part = secrets.token_hex(16)
    token_str = f"ctk_{encoded_url}_{secret_part}"
    expires_ts = time.time() + ttl_minutes * 60
    expires_iso = datetime.datetime.fromtimestamp(expires_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = get_db()
    conn.execute("DELETE FROM temp_tokens WHERE expires_at < ?", (time.time(),))
    conn.execute(
        "INSERT INTO temp_tokens (token, user_id, scope, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (token_str, user_id, scope, expires_ts, now_iso()),
    )
    conn.commit()
    conn.close()
    return {
        "token": token_str,
        "expires_at": expires_iso,
        "ttl_minutes": ttl_minutes,
        "server_url": server_url,
        "scope": scope,
    }


def verify_temp_token(
    authorization: Optional[str] = Header(default=None),
    x_cred_token: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> sqlite3.Row:
    raw = None
    if x_cred_token:
        raw = x_cred_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif token:
        raw = token.strip()
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Missing temporary token. Obtain one from the Remote MCP 'get_temp_token' tool first.",
        )
    conn = get_db()
    row = conn.execute(
        """
        SELECT t.token, t.user_id, t.scope, t.expires_at, u.username
        FROM temp_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token = ?
        """,
        (raw,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or unknown temporary token.")
    if row["expires_at"] < time.time():
        raise HTTPException(status_code=401, detail="Temporary token has expired. Request a new token via MCP.")
    return row


# ---------------------------------------------------------------------------
# FastAPI App Setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Blitz Credential Manager & Remote MCP Server",
    version="1.0.0",
    description="Secure credential & file manager with invite-code registration, Remote MCP server, and temporary token CLI.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    username: str
    password: str
    invite_code: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class EnvCredRequest(BaseModel):
    key: str
    value: str
    description: Optional[str] = ""


class EnvBulkRequest(BaseModel):
    dotenv: str


class TempTokenRequest(BaseModel):
    ttl_minutes: Optional[int] = 15


# ---------------------------------------------------------------------------
# Auth & User Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "blitz-cred-manager", "time": now_iso()}


@app.post("/api/auth/register")
def register_user(req: RegisterRequest):
    if req.invite_code.strip() != INVITE_CODE:
        raise HTTPException(status_code=403, detail="邀请码无效 (Invalid invite code)")
    username = req.username.strip()
    if len(username) < 2 or len(username) > 32:
        raise HTTPException(status_code=400, detail="用户名长度需在 2-32 个字符之间")
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="密码长度至少 4 位")

    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="该用户名已被注册")

    mcp_key = "mcp_" + secrets.token_hex(16)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, password_hash, is_admin, mcp_key, created_at) VALUES (?, ?, 0, ?, ?)",
        (username, hash_password(req.password), mcp_key, now_iso()),
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()

    token = create_session_token(user_id, username, False)
    return {
        "token": token,
        "user": {
            "id": user_id,
            "username": username,
            "is_admin": False,
            "mcp_key": mcp_key,
        },
    }


@app.post("/api/auth/login")
def login_user(req: LoginRequest):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (req.username.strip(),)).fetchone()
    conn.close()
    if not row or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_session_token(row["id"], row["username"], bool(row["is_admin"]))
    return {
        "token": token,
        "user": {
            "id": row["id"],
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "mcp_key": row["mcp_key"],
        },
    }


@app.post("/api/auth/change-password")
def change_password(req: ChangePasswordRequest, user: sqlite3.Row = Depends(get_current_user)):
    if not verify_password(req.old_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="当前原密码不正确")
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="新密码长度至少 4 位")
    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(req.new_password), user["id"]),
    )
    conn.commit()
    conn.close()
    return {"message": "密码修改成功"}


@app.get("/api/me")
def get_me(request: Request, user: sqlite3.Row = Depends(get_current_user)):
    base_url = get_public_base_url(request)
    mcp_key = user["mcp_key"]
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "mcp_key": mcp_key,
        "server_url": base_url,
        "github_repo_url": GITHUB_REPO_URL,
        "mcp_endpoints": {
            "streamable_http": f"{base_url}/mcp?key={mcp_key}",
            "streamable_http_default": f"{base_url}/mcp",
            "sse": f"{base_url}/sse?key={mcp_key}",
        },
        "cli_install": {
            "linux_macos_blitz": f"curl -fsSL {base_url}/cli/install.sh | sh",
            "windows_ps_blitz": f"irm {base_url}/cli/install.ps1 | iex",
            "linux_macos_github": f"curl -fsSL {GITHUB_RAW_BASE}/cli/install.sh | sh",
            "windows_ps_github": f"irm {GITHUB_RAW_BASE}/cli/install.ps1 | iex",
            "python_direct": f"curl -fsSL {base_url}/cli/cred-cli.py -o cred-cli.py && python3 cred-cli.py --help",
        },
    }


@app.post("/api/me/regenerate-mcp-key")
def regenerate_mcp_key(user: sqlite3.Row = Depends(get_current_user)):
    new_key = "mcp_" + secrets.token_hex(16)
    conn = get_db()
    conn.execute("UPDATE users SET mcp_key = ? WHERE id = ?", (new_key, user["id"]))
    conn.commit()
    conn.close()
    return {"mcp_key": new_key}


@app.get("/api/admin/users")
def list_all_users(user: sqlite3.Row = Depends(get_current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    conn = get_db()
    rows = conn.execute(
        """
        SELECT u.id, u.username, u.is_admin, u.created_at,
               (SELECT COUNT(*) FROM env_credentials e WHERE e.user_id = u.id) AS env_count,
               (SELECT COUNT(*) FROM file_credentials f WHERE f.user_id = u.id) AS file_count
        FROM users u
        ORDER BY u.id ASC
        """
    ).fetchall()
    conn.close()
    return {"users": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Environment Variable Credentials CRUD
# ---------------------------------------------------------------------------
@app.get("/api/credentials/env")
def list_env_credentials(user: sqlite3.Row = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, key, value, description, updated_at FROM env_credentials WHERE user_id = ? ORDER BY key ASC",
        (user["id"],),
    ).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/credentials/env")
def upsert_env_credential(req: EnvCredRequest, user: sqlite3.Row = Depends(get_current_user)):
    key = req.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="环境变量名 (Key) 不能为空")
    conn = get_db()
    conn.execute(
        """
        INSERT INTO env_credentials (user_id, key, value, description, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, key) DO UPDATE SET
            value = excluded.value,
            description = excluded.description,
            updated_at = excluded.updated_at
        """,
        (user["id"], key, req.value, req.description or "", now_iso()),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "key": key}


@app.post("/api/credentials/env/bulk")
def bulk_import_env(req: EnvBulkRequest, user: sqlite3.Row = Depends(get_current_user)):
    count = 0
    conn = get_db()
    for raw_line in req.dotenv.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        if not k:
            continue
        conn.execute(
            """
            INSERT INTO env_credentials (user_id, key, value, description, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (user["id"], k, v, "Bulk .env import", now_iso()),
        )
        count += 1
    conn.commit()
    conn.close()
    return {"status": "ok", "imported_count": count}


@app.delete("/api/credentials/env/{key}")
def delete_env_credential(key: str, user: sqlite3.Row = Depends(get_current_user)):
    conn = get_db()
    conn.execute("DELETE FROM env_credentials WHERE user_id = ? AND key = ?", (user["id"], key))
    conn.commit()
    conn.close()
    return {"status": "ok", "deleted": key}


# ---------------------------------------------------------------------------
# Credential Files CRUD (cookies.txt, config.json, etc.)
# ---------------------------------------------------------------------------
@app.get("/api/credentials/files")
def list_file_credentials(user: sqlite3.Row = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, filename, size, description, updated_at, content FROM file_credentials WHERE user_id = ? ORDER BY filename ASC",
        (user["id"],),
    ).fetchall()
    conn.close()
    items = []
    for r in rows:
        preview = None
        if r["size"] <= 65536 and r["content"] is not None:
            try:
                preview = bytes(r["content"]).decode("utf-8")
            except Exception:
                preview = None
        items.append(
            {
                "id": r["id"],
                "filename": r["filename"],
                "size": r["size"],
                "description": r["description"],
                "updated_at": r["updated_at"],
                "preview": preview,
            }
        )
    return {"items": items}


@app.post("/api/credentials/files")
async def upload_file_credential(
    request: Request,
    user: sqlite3.Row = Depends(get_current_user),
    file: Optional[UploadFile] = File(default=None),
    filename: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=""),
    content: Optional[str] = Form(default=None),
):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        fname = (body.get("filename") or "").strip()
        desc = body.get("description") or ""
        raw_text = body.get("content")
        if not fname or raw_text is None:
            raise HTTPException(status_code=400, detail="filename 和 content 不能为空")
        raw_bytes = raw_text.encode("utf-8") if isinstance(raw_text, str) else bytes(raw_text)
    else:
        if file is not None:
            fname = (filename or file.filename or "").strip()
            raw_bytes = await file.read()
        elif filename and content is not None:
            fname = filename.strip()
            raw_bytes = content.encode("utf-8")
        else:
            raise HTTPException(status_code=400, detail="请上传文件或提供文件名与内容")
        desc = description or ""

    fname = fname.lstrip("/\\")
    if not fname:
        raise HTTPException(status_code=400, detail="无效的文件名")

    conn = get_db()
    conn.execute(
        """
        INSERT INTO file_credentials (user_id, filename, content, size, description, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, filename) DO UPDATE SET
            content = excluded.content,
            size = excluded.size,
            description = excluded.description,
            updated_at = excluded.updated_at
        """,
        (user["id"], fname, raw_bytes, len(raw_bytes), desc, now_iso()),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "filename": fname, "size": len(raw_bytes)}


@app.get("/api/credentials/files/{filename:path}")
def download_file_web(filename: str, user: sqlite3.Row = Depends(get_current_user)):
    conn = get_db()
    row = conn.execute(
        "SELECT filename, content FROM file_credentials WHERE user_id = ? AND filename = ?",
        (user["id"], filename),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found")
    safe_name = os.path.basename(row["filename"])
    return Response(
        content=bytes(row["content"]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@app.delete("/api/credentials/files/{filename:path}")
def delete_file_credential(filename: str, user: sqlite3.Row = Depends(get_current_user)):
    conn = get_db()
    conn.execute("DELETE FROM file_credentials WHERE user_id = ? AND filename = ?", (user["id"], filename))
    conn.commit()
    conn.close()
    return {"status": "ok", "deleted": filename}


# ---------------------------------------------------------------------------
# Web Manual Temporary Token Generation
# ---------------------------------------------------------------------------
@app.post("/api/tokens/temp")
def create_temp_token_web(
    req: TempTokenRequest,
    request: Request,
    user: sqlite3.Row = Depends(get_current_user),
):
    base_url = get_public_base_url(request)
    info = generate_temp_token(user["id"], base_url, req.ttl_minutes or 15)
    t = info["token"]
    info["commands"] = {
        "list": f"cred-cli list --token {t}",
        "pull_env": f"cred-cli env --token {t} -o .env",
        "pull_file_example": f"cred-cli file config.json --token {t} -o ./config.json",
        "pull_all": f"cred-cli pull-all --token {t} --dir ./credentials",
        "run_with_env": f"cred-cli run --token {t} -- env",
    }
    return info


# ---------------------------------------------------------------------------
# CLI Endpoints (Authenticated via Temporary Token `ctk_...`)
# ---------------------------------------------------------------------------
@app.get("/api/cli/pull")
def cli_pull_bundle(token_row: sqlite3.Row = Depends(verify_temp_token)):
    user_id = token_row["user_id"]
    scope = token_row["scope"]
    conn = get_db()
    if scope == "all":
        env_rows = conn.execute(
            "SELECT key, value FROM env_credentials ORDER BY CASE WHEN user_id = ? THEN 1 ELSE 0 END ASC, id ASC",
            (user_id,),
        ).fetchall()
        file_rows_raw = conn.execute(
            "SELECT filename, size, description, updated_at FROM file_credentials ORDER BY CASE WHEN user_id = ? THEN 1 ELSE 0 END ASC, id ASC",
            (user_id,),
        ).fetchall()
        file_map = {r["filename"]: dict(r) for r in file_rows_raw}
        file_rows = list(file_map.values())
    else:
        env_rows = conn.execute(
            "SELECT key, value FROM env_credentials WHERE user_id = ? ORDER BY key ASC", (user_id,)
        ).fetchall()
        file_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT filename, size, description, updated_at FROM file_credentials WHERE user_id = ? ORDER BY filename ASC",
                (user_id,),
            ).fetchall()
        ]
    conn.close()
    expires_iso = datetime.datetime.fromtimestamp(token_row["expires_at"], tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "username": token_row["username"],
        "expires_at": expires_iso,
        "env": {r["key"]: r["value"] for r in env_rows},
        "files": file_rows,
    }


@app.get("/api/cli/file/{filename:path}")
def cli_pull_file(filename: str, token_row: sqlite3.Row = Depends(verify_temp_token)):
    user_id = token_row["user_id"]
    scope = token_row["scope"]
    conn = get_db()
    row = conn.execute(
        "SELECT filename, content FROM file_credentials WHERE user_id = ? AND filename = ?",
        (user_id, filename),
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT filename, content FROM file_credentials WHERE user_id = ? AND filename LIKE ?",
            (user_id, f"%{os.path.basename(filename)}"),
        ).fetchone()
    if not row and scope == "all":
        row = conn.execute(
            "SELECT filename, content FROM file_credentials WHERE filename = ? ORDER BY id DESC LIMIT 1",
            (filename,),
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT filename, content FROM file_credentials WHERE filename LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%{os.path.basename(filename)}",),
            ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Credential file '{filename}' not found for user '{token_row['username']}'")
    safe_name = os.path.basename(row["filename"])
    return Response(
        content=bytes(row["content"]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# ---------------------------------------------------------------------------
# CLI Distribution & Installer Endpoints (/cli/...)
# ---------------------------------------------------------------------------
@app.get("/cli/install.sh")
def serve_install_sh(request: Request):
    base_url = get_public_base_url(request)
    script_path = CLI_DIR / "install.sh"
    content = script_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("__DEFAULT_SERVER_URL__", base_url)
    return PlainTextResponse(content, media_type="text/x-shellscript")


@app.get("/cli/install.ps1")
def serve_install_ps1(request: Request):
    base_url = get_public_base_url(request)
    script_path = CLI_DIR / "install.ps1"
    content = script_path.read_text(encoding="utf-8").replace("__DEFAULT_SERVER_URL__", base_url)
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


@app.get("/cli/cred-cli.py")
def serve_cred_cli_py(request: Request):
    base_url = get_public_base_url(request)
    script_path = CLI_DIR / "cred-cli.py"
    content = script_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("__DEFAULT_SERVER_URL__", base_url)
    return PlainTextResponse(content, media_type="text/x-python")


@app.get("/cli/{binary_name}")
def serve_cli_binary(binary_name: str, request: Request):
    bin_path = CLI_DIR / "bin" / os.path.basename(binary_name)
    if bin_path.exists() and bin_path.is_file():
        return FileResponse(
            str(bin_path),
            media_type="application/octet-stream",
            filename=os.path.basename(binary_name),
        )
    if binary_name in ("cred-cli", "cred-cli.py"):
        return serve_cred_cli_py(request)
    raise HTTPException(status_code=404, detail=f"CLI artifact '{binary_name}' not found")


# ---------------------------------------------------------------------------
# Remote MCP Server Implementation (Streamable HTTP `/mcp` & SSE `/sse`)
# ---------------------------------------------------------------------------
def build_mcp_instructions(server_url: str) -> str:
    return (
        "Blitz Credential Manager Remote MCP Server.\n"
        "⚠️ MANDATORY STEP FOR AI AGENTS BEFORE PULLING CREDENTIALS:\n"
        "You MUST ensure the `cred-cli` command-line tool is installed in your local shell environment before using the temporary token to pull environment variables or credential files.\n\n"
        "1. Install `cred-cli` (choose one):\n"
        f"   - Linux / macOS (from Blitz frontend): curl -fsSL {server_url}/cli/install.sh | sh\n"
        f"   - Linux / macOS (from GitHub repo):    curl -fsSL {GITHUB_RAW_BASE}/cli/install.sh | sh\n"
        f"   - Windows PowerShell (Blitz frontend): irm {server_url}/cli/install.ps1 | iex\n"
        f"   - Windows PowerShell (GitHub repo):    irm {GITHUB_RAW_BASE}/cli/install.ps1 | iex\n"
        f"   - Universal Python script fallback:    curl -fsSL {server_url}/cli/cred-cli.py -o cred-cli.py\n"
        f"   - GitHub Repository: {GITHUB_REPO_URL}\n\n"
        "2. Workflow to fetch credentials:\n"
        "   - Step A: Call MCP tool `list_credentials` to inspect available environment variable keys and credential filenames (e.g. cookies.txt, config.json).\n"
        "   - Step B: Call MCP tool `get_temp_token` to obtain a short-lived temporary token (`ctk_...`).\n"
        "   - Step C: Execute `cred-cli` in your local terminal with `--token <TEMP_TOKEN>`:\n"
        "     * Pull all env vars to .env: `cred-cli env --token <TEMP_TOKEN> -o .env`\n"
        "     * Pull single env var:       `cred-cli env --token <TEMP_TOKEN> --key <KEY_NAME>`\n"
        "     * Pull a credential file:    `cred-cli file <FILENAME> --token <TEMP_TOKEN> -o ./<FILENAME>`\n"
        "     * Pull everything at once:   `cred-cli pull-all --token <TEMP_TOKEN> --dir ./credentials`\n"
        "     * Run command with env vars: `cred-cli run --token <TEMP_TOKEN> -- <command>`"
    )


def is_explicit_mcp_scope(request: Request, explicit_key: Optional[str], explicit_username: Optional[str]) -> bool:
    if explicit_key or explicit_username or request.query_params.get("key") or request.headers.get("x-mcp-key"):
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer ") and auth_header[7:].strip().startswith("mcp_"):
        return True
    return False


def resolve_mcp_user(
    request: Request,
    explicit_key: Optional[str] = None,
    explicit_username: Optional[str] = None,
) -> sqlite3.Row:
    mcp_key = explicit_key or request.query_params.get("key") or request.headers.get("x-mcp-key")
    auth_header = request.headers.get("authorization", "")
    if not mcp_key and auth_header.lower().startswith("bearer "):
        candidate = auth_header[7:].strip()
        if candidate.startswith("mcp_"):
            mcp_key = candidate

    conn = get_db()
    if mcp_key:
        row = conn.execute("SELECT * FROM users WHERE mcp_key = ?", (mcp_key.strip(),)).fetchone()
        conn.close()
        if not row:
            raise ValueError(f"Invalid MCP key: {mcp_key}")
        return row

    if explicit_username:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (explicit_username.strip(),)).fetchone()
        conn.close()
        if not row:
            raise ValueError(f"User '{explicit_username}' not found")
        return row

    # If no key or username is provided, prefer the most recently active user who has credentials, or fallback to 'admin'
    row = conn.execute(
        """
        SELECT u.* FROM users u
        LEFT JOIN env_credentials e ON e.user_id = u.id
        LEFT JOIN file_credentials f ON f.user_id = u.id
        GROUP BY u.id
        ORDER BY (COUNT(e.id) + COUNT(f.id)) DESC, u.id ASC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        row = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
    conn.close()
    return row


def get_mcp_tools_schema(server_url: str) -> List[Dict[str, Any]]:
    return [
        {
            "name": "get_cli_install_guide",
            "description": (
                "IMPORTANT: Call this tool or follow its instructions to install the `cred-cli` command-line tool BEFORE pulling credentials. "
                f"Returns one-line CLI installation commands for Linux, macOS, Windows, and Python from both Blitz ({server_url}/cli/) and GitHub ({GITHUB_REPO_URL})."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "list_credentials",
            "description": (
                "List all available environment variable keys and credential files (e.g., cookies.txt, config.json) stored in Blitz Credential Manager without exposing secret values. "
                "NOTE: Before pulling actual values or files, make sure `cred-cli` is installed (`curl -fsSL "
                f"{server_url}/cli/install.sh | sh` or from {GITHUB_REPO_URL}), then call `get_temp_token`."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "Optional username to query credentials for (defaults to the authenticated MCP user).",
                    }
                },
            },
        },
        {
            "name": "get_temp_token",
            "description": (
                "Issue a short-lived temporary token (`ctk_...`) so the AI agent can pull environment variables or credential files using the `cred-cli` command-line tool. "
                "⚠️ PREREQUISITE: You MUST install `cred-cli` first if not installed: "
                f"`curl -fsSL {server_url}/cli/install.sh | sh` (Linux/macOS) or `irm {server_url}/cli/install.ps1 | iex` (Windows) or from GitHub `{GITHUB_REPO_URL}`."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ttl_minutes": {
                        "type": "integer",
                        "description": "Token validity duration in minutes (default: 15, max: 120).",
                        "default": 15,
                    },
                    "username": {
                        "type": "string",
                        "description": "Optional username to issue the temporary token for (defaults to the authenticated MCP user).",
                    },
                },
            },
        },
    ]


def handle_mcp_jsonrpc(request: Request, msg: Dict[str, Any], path_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    server_url = get_public_base_url(request)

    # Notifications have no id and expect no response
    if msg_id is None and method.startswith("notifications/"):
        return None

    if method == "initialize":
        client_proto = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_proto,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "prompts": {"listChanged": False},
                    "resources": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "blitz-cred-manager-mcp",
                    "version": "1.0.0",
                },
                "instructions": build_mcp_instructions(server_url),
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": get_mcp_tools_schema(server_url)},
        }

    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "prompts": [
                    {
                        "name": "pull-credentials-guide",
                        "description": "Step-by-step instructions for installing cred-cli and pulling credentials with a temporary MCP token.",
                    }
                ]
            },
        }

    if method == "prompts/get":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "description": "How to install cred-cli and pull credentials",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": build_mcp_instructions(server_url)},
                    }
                ],
            },
        }

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "resources": [
                    {
                        "uri": "cred://cli/install-guide",
                        "name": "cred-cli Installation & Usage Guide",
                        "mimeType": "text/markdown",
                        "description": "Instructions to install cred-cli from Blitz frontend or GitHub before pulling credentials.",
                    }
                ]
            },
        }

    if method == "resources/read":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "contents": [
                    {
                        "uri": params.get("uri", "cred://cli/install-guide"),
                        "mimeType": "text/markdown",
                        "text": build_mcp_instructions(server_url),
                    }
                ]
            },
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}

        try:
            if tool_name == "get_cli_install_guide":
                guide = {
                    "important_notice": "You MUST install `cred-cli` before running credential pull commands with the temporary token.",
                    "server_url": server_url,
                    "github_repository": GITHUB_REPO_URL,
                    "install_commands": {
                        "linux_macos_from_blitz": f"curl -fsSL {server_url}/cli/install.sh | sh",
                        "linux_macos_from_github": f"curl -fsSL {GITHUB_RAW_BASE}/cli/install.sh | sh",
                        "windows_powershell_from_blitz": f"irm {server_url}/cli/install.ps1 | iex",
                        "windows_powershell_from_github": f"irm {GITHUB_RAW_BASE}/cli/install.ps1 | iex",
                        "python_zero_dep_script": f"curl -fsSL {server_url}/cli/cred-cli.py -o cred-cli.py && python3 cred-cli.py --help",
                    },
                    "direct_binary_urls": {
                        "linux_amd64": f"{server_url}/cli/cred-cli-linux-amd64",
                        "linux_arm64": f"{server_url}/cli/cred-cli-linux-arm64",
                        "windows_amd64": f"{server_url}/cli/cred-cli-windows-amd64.exe",
                        "darwin_amd64": f"{server_url}/cli/cred-cli-darwin-amd64",
                        "darwin_arm64": f"{server_url}/cli/cred-cli-darwin-arm64",
                    },
                }
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(guide, indent=2, ensure_ascii=False)}],
                        "isError": False,
                    },
                }

            elif tool_name == "list_credentials":
                target_user = resolve_mcp_user(request, path_key, tool_args.get("username"))
                explicit = is_explicit_mcp_scope(request, path_key, tool_args.get("username"))
                conn = get_db()
                if explicit:
                    env_rows = conn.execute(
                        "SELECT key, description, updated_at FROM env_credentials WHERE user_id = ? ORDER BY key ASC",
                        (target_user["id"],),
                    ).fetchall()
                    file_rows = conn.execute(
                        "SELECT filename, size, description, updated_at FROM file_credentials WHERE user_id = ? ORDER BY filename ASC",
                        (target_user["id"],),
                    ).fetchall()
                else:
                    env_rows = conn.execute(
                        "SELECT e.key, e.description, e.updated_at, u.username AS owner FROM env_credentials e JOIN users u ON u.id = e.user_id ORDER BY e.key ASC"
                    ).fetchall()
                    file_rows = conn.execute(
                        "SELECT f.filename, f.size, f.description, f.updated_at, u.username AS owner FROM file_credentials f JOIN users u ON u.id = f.user_id ORDER BY f.filename ASC"
                    ).fetchall()
                conn.close()
                payload = {
                    "username": target_user["username"],
                    "scope": "user" if explicit else "all",
                    "cli_prerequisite_notice": (
                        f"To pull these credentials, first ensure `cred-cli` is installed (`curl -fsSL {server_url}/cli/install.sh | sh` "
                        f"or from GitHub `{GITHUB_REPO_URL}`), then call `get_temp_token`."
                    ),
                    "environment_variables": [dict(r) for r in env_rows],
                    "credential_files": [dict(r) for r in file_rows],
                }
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}],
                        "isError": False,
                    },
                }

            elif tool_name == "get_temp_token":
                target_user = resolve_mcp_user(request, path_key, tool_args.get("username"))
                explicit = is_explicit_mcp_scope(request, path_key, tool_args.get("username"))
                scope = "user" if explicit else "all"
                ttl = int(tool_args.get("ttl_minutes") or 15)
                token_info = generate_temp_token(target_user["id"], server_url, ttl, scope=scope)
                t = token_info["token"]

                conn = get_db()
                if explicit:
                    env_keys = [
                        r["key"]
                        for r in conn.execute(
                            "SELECT key FROM env_credentials WHERE user_id = ? ORDER BY key ASC", (target_user["id"],)
                        ).fetchall()
                    ]
                    file_names = [
                        r["filename"]
                        for r in conn.execute(
                            "SELECT filename FROM file_credentials WHERE user_id = ? ORDER BY filename ASC",
                            (target_user["id"],),
                        ).fetchall()
                    ]
                else:
                    env_keys = [
                        r["key"]
                        for r in conn.execute("SELECT DISTINCT key FROM env_credentials ORDER BY key ASC").fetchall()
                    ]
                    file_names = [
                        r["filename"]
                        for r in conn.execute("SELECT DISTINCT filename FROM file_credentials ORDER BY filename ASC").fetchall()
                    ]
                conn.close()

                file_cmds = [f"cred-cli file \"{fn}\" --token {t} -o \"./{os.path.basename(fn)}\"" for fn in file_names]
                response_data = {
                    "username": target_user["username"],
                    "token": t,
                    "expires_at": token_info["expires_at"],
                    "ttl_minutes": token_info["ttl_minutes"],
                    "server_url": server_url,
                    "step_1_install_cli_if_needed": {
                        "check_installed": "cred-cli version",
                        "linux_macos_install": f"curl -fsSL {server_url}/cli/install.sh | sh",
                        "windows_powershell_install": f"irm {server_url}/cli/install.ps1 | iex",
                        "github_repo": GITHUB_REPO_URL,
                        "python_fallback": f"curl -fsSL {server_url}/cli/cred-cli.py -o cred-cli.py && python3 cred-cli.py list --token {t}",
                    },
                    "step_2_pull_credentials_with_cli": {
                        "list_available": f"cred-cli list --token {t}",
                        "pull_all_env_to_dotenv": f"cred-cli env --token {t} -o .env",
                        "pull_single_env": (
                            f"cred-cli env --token {t} --key {env_keys[0]}"
                            if env_keys
                            else f"cred-cli env --token {t} --key <KEY>"
                        ),
                        "pull_specific_files": file_cmds
                        if file_cmds
                        else [f"cred-cli file <filename> --token {t} -o ./<filename>"],
                        "pull_everything": f"cred-cli pull-all --token {t} --dir ./credentials",
                        "run_command_with_env": f"cred-cli run --token {t} -- <your_command>",
                    },
                    "available_env_keys": env_keys,
                    "available_files": file_names,
                }
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(response_data, indent=2, ensure_ascii=False)}],
                        "isError": False,
                    },
                }

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not supported: {method}"},
    }


# Streamable HTTP MCP Endpoint (/mcp and /mcp/{mcp_key})
@app.post("/mcp")
@app.post("/mcp/{mcp_key}")
async def mcp_streamable_post(request: Request, mcp_key: Optional[str] = None):
    body = await request.json()
    if isinstance(body, list):
        responses = []
        for item in body:
            res = handle_mcp_jsonrpc(request, item, mcp_key)
            if res is not None:
                responses.append(res)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)
    else:
        res = handle_mcp_jsonrpc(request, body, mcp_key)
        if res is None:
            return Response(status_code=202)
        return JSONResponse(res)


@app.get("/mcp")
@app.get("/mcp/{mcp_key}")
async def mcp_streamable_get(request: Request, mcp_key: Optional[str] = None):
    accept = request.headers.get("accept", "")
    server_url = get_public_base_url(request)
    if "text/event-stream" in accept:
        async def event_gen():
            while True:
                if await request.is_disconnected():
                    break
                yield ": keepalive\n\n"
                await asyncio.sleep(15)

        return StreamingResponse(event_gen(), media_type="text/event-stream")
    return JSONResponse(
        {
            "name": "blitz-cred-manager-mcp",
            "version": "1.0.0",
            "transport": "streamable-http",
            "endpoint": f"{server_url}/mcp",
            "sse_endpoint": f"{server_url}/sse",
            "github_repo": GITHUB_REPO_URL,
            "instructions": build_mcp_instructions(server_url),
        }
    )


@app.delete("/mcp")
@app.delete("/mcp/{mcp_key}")
async def mcp_streamable_delete():
    return Response(status_code=200)


# Classic HTTP+SSE MCP Transport (/sse and /messages)
SSE_SESSIONS: Dict[str, asyncio.Queue] = {}


@app.get("/sse")
@app.get("/sse/{mcp_key}")
async def mcp_sse_endpoint(request: Request, mcp_key: Optional[str] = None):
    session_id = secrets.token_hex(16)
    queue: asyncio.Queue = asyncio.Queue()
    SSE_SESSIONS[session_id] = queue
    key_q = f"&key={mcp_key}" if mcp_key else (f"&key={request.query_params.get('key')}" if request.query_params.get("key") else "")
    post_uri = f"/messages?session_id={session_id}{key_q}"

    async def sse_generator():
        try:
            yield f"event: endpoint\ndata: {post_uri}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            SSE_SESSIONS.pop(session_id, None)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/messages")
async def mcp_sse_messages(request: Request, session_id: str = Query(...), key: Optional[str] = Query(default=None)):
    body = await request.json()
    res = handle_mcp_jsonrpc(request, body, key)
    queue = SSE_SESSIONS.get(session_id)
    if queue and res is not None:
        await queue.put(res)
    if res is not None:
        return JSONResponse(res, status_code=202)
    return Response(status_code=202)


# ---------------------------------------------------------------------------
# Web UI Frontend
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = STATIC_DIR / "index.html"
    return HTMLResponse(index_file.read_text(encoding="utf-8"))
