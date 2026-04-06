"""
gmailk-V BioHash Template Server
RPi 端模板儲存 + 管理 Web UI

啟動方式:
  cd gmailk-VVeb/py
  uv run python main.py

環境變數:
  DATABASE_URL=postgres://gvw:gvw@localhost/gvw  (預設)
  PORT=3000                                       (預設)
"""

import asyncio
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator
import os

# ─── Config ───

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://gvw:gvw@localhost/gvw")
PORT = int(os.getenv("PORT", "3000"))
INDEX_HTML = Path(__file__).parent.parent / "index.html"

# ─── Models ───

class CreatePersonRequest(BaseModel):
    name: str
    biohash_template: str
    description: str = ""
    encrypted_payload: str = ""

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("名稱不能為空")
        return v.strip()

    @field_validator("biohash_template")
    @classmethod
    def validate_template(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("模板不能為空")
        if len(v) % 2 != 0:
            raise ValueError("模板 hex 長度必須為偶數")
        if not re.fullmatch(r"[0-9a-fA-F]+", v):
            raise ValueError("模板必須為有效的 hex 字串")
        return v

    @field_validator("encrypted_payload")
    @classmethod
    def validate_payload(cls, v: str) -> str:
        return v.strip()


class UpdatePersonRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class PersonResponse(BaseModel):
    id: int
    name: str
    biohash_template: str
    description: str
    encrypted_payload: str
    template_bytes: int
    created_at: str | None
    updated_at: str | None


class StatusResponse(BaseModel):
    uptime_seconds: int
    person_count: int
    db_connected: bool
    version: str


# ─── App ───

_start_time = time.monotonic()
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    assert _pool is not None, "Database pool not initialized"
    return _pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    print(f"📡 連接資料庫: {DATABASE_URL}")

    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    # 自動建表
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS persons (
                id                SERIAL PRIMARY KEY,
                name              VARCHAR(255) NOT NULL,
                biohash_template  TEXT NOT NULL,
                description       TEXT DEFAULT '',
                encrypted_payload TEXT DEFAULT '',
                created_at        TIMESTAMPTZ DEFAULT NOW(),
                updated_at        TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    print("✅ 資料庫就緒")
    print(f"🚀 伺服器運行中: http://0.0.0.0:{PORT}")

    yield

    await _pool.close()
    print("👋 伺服器關閉")


app = FastAPI(title="gmailk-V Template Server", version="0.2.0", lifespan=lifespan)


# ─── Helpers ───

def row_to_response(row: asyncpg.Record) -> PersonResponse:
    return PersonResponse(
        id=row["id"],
        name=row["name"],
        biohash_template=row["biohash_template"],
        description=row["description"] or "",
        encrypted_payload=row["encrypted_payload"] or "",
        template_bytes=len(row["biohash_template"]) // 2,
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
    )


# ─── Routes: Web UI ───

@app.get("/", response_class=HTMLResponse)
async def serve_html():
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


# ─── Routes: CRUD ───

@app.get("/api/persons", response_model=list[PersonResponse])
async def list_persons():
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM persons ORDER BY id"
    )
    return [row_to_response(r) for r in rows]


@app.post("/api/persons", response_model=PersonResponse, status_code=201)
async def create_person(req: CreatePersonRequest):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO persons (name, biohash_template, description, encrypted_payload)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        req.name,
        req.biohash_template,
        req.description,
        req.encrypted_payload,
    )
    print(f"✅ 新增人員: [{row['id']}] {row['name']} (模板 {len(req.biohash_template)//2} bytes)")
    return row_to_response(row)


@app.get("/api/persons/{person_id}", response_model=PersonResponse)
async def get_person(person_id: int):
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM persons WHERE id = $1", person_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return row_to_response(row)


@app.put("/api/persons/{person_id}", response_model=PersonResponse)
async def update_person(person_id: int, req: UpdatePersonRequest):
    pool = await get_pool()
    existing = await pool.fetchrow("SELECT * FROM persons WHERE id = $1", person_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Person not found")

    new_name = req.name if req.name is not None else existing["name"]
    new_desc = req.description if req.description is not None else existing["description"]

    row = await pool.fetchrow(
        """
        UPDATE persons SET name = $1, description = $2, updated_at = NOW()
        WHERE id = $3
        RETURNING *
        """,
        new_name,
        new_desc,
        person_id,
    )
    return row_to_response(row)


@app.delete("/api/persons/{person_id}", status_code=204)
async def delete_person(person_id: int):
    pool = await get_pool()
    result = await pool.execute("DELETE FROM persons WHERE id = $1", person_id)
    # result is e.g. "DELETE 1" or "DELETE 0"
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Person not found")
    print(f"🗑️ 刪除人員 ID: {person_id}")


# ─── Routes: Status ───

@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    pool = await get_pool()
    try:
        count = await pool.fetchval("SELECT COUNT(*) FROM persons")
        db_ok = True
    except Exception:
        count = 0
        db_ok = False

    return StatusResponse(
        uptime_seconds=int(time.monotonic() - _start_time),
        person_count=count,
        db_connected=db_ok,
        version="0.2.0",
    )


# ─── Main ───

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
        log_level="info",
    )
