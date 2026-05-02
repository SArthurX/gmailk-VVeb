"""
gmailk-V BioHash Template Server
RPi 端模板儲存 + 管理 Web UI

啟動方式:
  cd gmailk-VVeb/py
  uv run python main.py

環境變數:
  DATABASE_PATH=gvw.db  (預設，相對於 main.py 所在目錄)
  PORT=3000             (預設)

API 概覽:
  GET    /                           Web UI
  POST   /api/enroll                 註冊: 上傳照片 + 資訊 + 日期種子
  GET    /api/persons                列出所有人員 (Web UI 管理)
  GET    /api/persons/{id}           取得單一人員
  PUT    /api/persons/{id}           更新名稱/描述
  DELETE /api/persons/{id}           刪除人員
  POST   /api/persons/{id}/complete  裝置回傳: 填入碼字完成註冊
  GET    /api/templates              CV181X 專用: 只取已完成的碼字
  GET    /api/pending                CV181X 專用: 只取 pending 照片資訊（輕量）
  POST   /api/persons                CV181X 裝置端: 直接寫入完成的碼字
"""

import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, field_validator
import os

# ─── Config ───

DATABASE_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).parent / "gvw.db"))
PORT = int(os.getenv("PORT", "8787"))
INDEX_HTML = Path(__file__).parent.parent / "index.html"
UPLOAD_DIR = Path(__file__).parent / "uploads"

# ─── Models ───


class EnrollResponse(BaseModel):
    id: int
    name: str
    description: str
    photo_path: str
    valid_date: str
    status: str
    created_at: str | None


class CompleteRequest(BaseModel):
    """CV181X 裝置完成註冊時回傳的碼字"""
    biohash_template: str
    encrypted_payload: str = ""

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


class DeviceCreateRequest(BaseModel):
    """CV181X 裝置端直接寫入完成的碼字 (按鈕註冊流程)"""
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


class UpdatePersonRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class PersonResponse(BaseModel):
    id: int
    name: str
    description: str
    photo_path: str
    valid_date: str
    status: str
    biohash_template: str
    encrypted_payload: str
    template_bytes: int
    created_at: str | None
    updated_at: str | None


class TemplateResponse(BaseModel):
    """CV181X 驗證用：只包含碼字和必要資訊"""
    id: int
    name: str
    biohash_template: str
    encrypted_payload: str
    template_bytes: int


class StatusResponse(BaseModel):
    uptime_seconds: int
    person_count: int
    pending_count: int
    db_connected: bool
    version: str


class PendingResponse(BaseModel):
    """CV181X 裝置用：只包含 pending 記錄的最小資訊 + 有效日期"""
    id: int
    name: str
    photo_path: str
    valid_date: str


# ─── App ───

_start_time = time.monotonic()
_db_path: str = DATABASE_PATH


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📡 資料庫路徑: {_db_path}")
    print(f"📁 照片目錄: {UPLOAD_DIR}")

    # 自動建表
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS persons (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                description       TEXT DEFAULT '',
                photo_path        TEXT DEFAULT '',
                valid_date        TEXT DEFAULT '',
                status            TEXT DEFAULT 'pending',
                biohash_template  TEXT DEFAULT '',
                encrypted_payload TEXT DEFAULT '',
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.commit()

    print("✅ 資料庫就緒")
    print(f"🚀 伺服器運行中: http://0.0.0.0:{PORT}")

    yield

    print("👋 伺服器關閉")


app = FastAPI(title="gmailk-V Template Server", version="0.4.0", lifespan=lifespan)


# ─── Helpers ───

def row_to_person(row: aiosqlite.Row) -> PersonResponse:
    tmpl = row["biohash_template"] or ""
    return PersonResponse(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        photo_path=row["photo_path"] or "",
        valid_date=row["valid_date"] or "",
        status=row["status"] or "pending",
        biohash_template=tmpl,
        encrypted_payload=row["encrypted_payload"] or "",
        template_bytes=len(tmpl) // 2 if tmpl else 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_template(row: aiosqlite.Row) -> TemplateResponse:
    tmpl = row["biohash_template"] or ""
    return TemplateResponse(
        id=row["id"],
        name=row["name"],
        biohash_template=tmpl,
        encrypted_payload=row["encrypted_payload"] or "",
        template_bytes=len(tmpl) // 2 if tmpl else 0,
    )


# ─── Routes: Web UI ───

@app.get("/", response_class=HTMLResponse)
async def serve_html():
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    """提供上傳照片的靜態存取"""
    file_path = UPLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # 安全性: 確保路徑沒有目錄遍歷
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return FileResponse(file_path)


# ─── Routes: 註冊 (Enroll) ───

@app.post("/api/enroll", response_model=EnrollResponse, status_code=201)
async def enroll_person(
    name: str = Form(...),
    description: str = Form(""),
    valid_date: str = Form(...),
    photo: UploadFile = File(...),
):
    """
    Web UI 註冊流程:
    1. 使用者上傳照片 + 個人資訊 + 有效日期
    2. 照片存於 RPi，建立 pending 記錄
    3. 等待 CV181X 裝置連線後處理 (Phase 2)
    4. 裝置處理完成後透過 POST /api/persons/{id}/complete 回傳碼字
    """
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="名稱不能為空")

    # 驗證 valid_date 格式：12 位數字 (YYYYMMDDHHmm)
    if not re.fullmatch(r"\d{12}", valid_date):
        raise HTTPException(status_code=422, detail="有效日期格式錯誤，應為 12 位數字 (YYYYMMDDHHmm)")
    
    # 驗證層級一致性
    year = int(valid_date[:4])
    month = int(valid_date[4:6])
    day = int(valid_date[6:8])
    hour = int(valid_date[8:10])
    minute = int(valid_date[10:12])
    
    if year < 2020:
        raise HTTPException(status_code=422, detail="年份不能小於 2020")
    if month > 12:
        raise HTTPException(status_code=422, detail="月份無效")
    if day > 31:
        raise HTTPException(status_code=422, detail="日期無效")
    if hour > 23:
        raise HTTPException(status_code=422, detail="小時無效")
    if minute > 59:
        raise HTTPException(status_code=422, detail="分鐘無效")
    # 層級一致性：月=0 則日/時/分必須為 0
    if month == 0 and (day != 0 or hour != 0 or minute != 0):
        raise HTTPException(status_code=422, detail="月份未設定時，日/時/分也必須未設定")
    if day == 0 and (hour != 0 or minute != 0):
        raise HTTPException(status_code=422, detail="日期未設定時，時/分也必須未設定")
    if hour == 0 and minute != 0:
        raise HTTPException(status_code=422, detail="小時未設定時，分鐘也必須未設定")

    # 儲存照片
    photo_id = str(uuid.uuid4())
    ext = Path(photo.filename or "photo.jpg").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        ext = ".jpg"
    photo_filename = f"{photo_id}{ext}"
    photo_path = UPLOAD_DIR / photo_filename

    content = await photo.read()
    if len(content) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=413, detail="照片檔案過大 (上限 10MB)")
        
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(content))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
        
        # 強制存成 jpg
        photo_filename = f"{photo_id}.jpg"
        photo_path = UPLOAD_DIR / photo_filename
        img.save(photo_path, format="JPEG", quality=85)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"圖片處理失敗: {str(e)}")

    # 建立 pending 記錄
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            INSERT INTO persons (name, description, photo_path, valid_date, status)
            VALUES (?, ?, ?, ?, 'pending')
            RETURNING id, name, description, photo_path, valid_date, status, created_at
            """,
            (name, description.strip(), photo_filename, valid_date),
        ) as cursor:
            row = await cursor.fetchone()
        await db.commit()

    print(f"📸 註冊請求: [{row['id']}] {name} (有效日期 {valid_date}, 待裝置處理)")

    return EnrollResponse(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        photo_path=row["photo_path"],
        valid_date=row["valid_date"],
        status=row["status"],
        created_at=row["created_at"],
    )


@app.post("/api/persons/{person_id}/complete", response_model=PersonResponse)
async def complete_enrollment(person_id: int, req: CompleteRequest):
    """
    CV181X 裝置完成處理後回傳碼字。
    裝置流程: 讀取照片 → ArcFace → BioHash → Fuzzy Commitment → 碼字
    """
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute("SELECT * FROM persons WHERE id = ?", (person_id,)) as cursor:
            existing = await cursor.fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Person not found")
        if existing["status"] == "completed":
            raise HTTPException(status_code=409, detail="Already completed")

        async with db.execute(
            """
            UPDATE persons
            SET biohash_template = ?, encrypted_payload = ?,
                status = 'completed', updated_at = datetime('now')
            WHERE id = ?
            RETURNING *
            """,
            (req.biohash_template, req.encrypted_payload, person_id),
        ) as cursor:
            row = await cursor.fetchone()
        await db.commit()

    print(f"✅ 註冊完成: [{person_id}] {row['name']} (模板 {len(req.biohash_template)//2} bytes)")
    return row_to_person(row)


# ─── Routes: CRUD ───

@app.get("/api/persons", response_model=list[PersonResponse])
async def list_persons():
    """Web UI: 列出所有人員（含 pending 和 completed）"""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM persons ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
    return [row_to_person(r) for r in rows]


@app.get("/api/templates", response_model=list[TemplateResponse])
async def list_templates():
    """CV181X 專用: 只取已完成的碼字用於驗證"""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM persons WHERE status = 'completed' AND biohash_template != '' ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()
    return [row_to_template(r) for r in rows]


@app.get("/api/pending", response_model=list[PendingResponse])
async def list_pending():
    """CV181X 專用: 只取 pending 記錄的 {id, name, photo_path, valid_date}"""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, photo_path, valid_date FROM persons WHERE status = 'pending' AND photo_path != '' ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()
    return [
        PendingResponse(
            id=r["id"], name=r["name"], photo_path=r["photo_path"],
            valid_date=r["valid_date"] or ""
        )
        for r in rows
    ]


@app.post("/api/persons", response_model=PersonResponse, status_code=201)
async def create_person_direct(req: DeviceCreateRequest):
    """
    CV181X 裝置端直接寫入 (按鈕長按註冊流程)。
    碼字由裝置本地生成，直接存入 RPi，狀態為 completed。
    """
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            INSERT INTO persons (name, biohash_template, description, encrypted_payload, status)
            VALUES (?, ?, ?, ?, 'completed')
            RETURNING *
            """,
            (req.name, req.biohash_template, req.description, req.encrypted_payload),
        ) as cursor:
            row = await cursor.fetchone()
        await db.commit()
    print(f"✅ 裝置直接註冊: [{row['id']}] {row['name']} (模板 {len(req.biohash_template)//2} bytes)")
    return row_to_person(row)


@app.get("/api/persons/{person_id}", response_model=PersonResponse)
async def get_person(person_id: int):
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM persons WHERE id = ?", (person_id,)) as cursor:
            row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return row_to_person(row)


@app.put("/api/persons/{person_id}", response_model=PersonResponse)
async def update_person(person_id: int, req: UpdatePersonRequest):
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute("SELECT * FROM persons WHERE id = ?", (person_id,)) as cursor:
            existing = await cursor.fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Person not found")

        new_name = req.name if req.name is not None else existing["name"]
        new_desc = req.description if req.description is not None else existing["description"]

        async with db.execute(
            """
            UPDATE persons SET name = ?, description = ?, updated_at = datetime('now')
            WHERE id = ?
            RETURNING *
            """,
            (new_name, new_desc, person_id),
        ) as cursor:
            row = await cursor.fetchone()
        await db.commit()
    return row_to_person(row)


@app.delete("/api/persons/{person_id}", status_code=204)
async def delete_person(person_id: int):
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row

        # 取得照片路徑，刪除照片檔案
        async with db.execute("SELECT photo_path FROM persons WHERE id = ?", (person_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Person not found")

        if row["photo_path"]:
            photo_file = UPLOAD_DIR / row["photo_path"]
            if photo_file.exists():
                photo_file.unlink()

        cursor = await db.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        await db.commit()
    print(f"🗑️ 刪除人員 ID: {person_id}")


# ─── Routes: Status ───

@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    try:
        async with aiosqlite.connect(_db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM persons") as cursor:
                row = await cursor.fetchone()
                total = row[0]
            async with db.execute("SELECT COUNT(*) FROM persons WHERE status = 'pending'") as cursor:
                row = await cursor.fetchone()
                pending = row[0]
        db_ok = True
    except Exception:
        total = 0
        pending = 0
        db_ok = False

    return StatusResponse(
        uptime_seconds=int(time.monotonic() - _start_time),
        person_count=total,
        pending_count=pending,
        db_connected=db_ok,
        version="0.4.0",
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
