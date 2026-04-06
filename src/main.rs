use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{Html, IntoResponse},
    routing::{delete, get, post, put},
    Json, Router,
};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use sqlx::postgres::PgPoolOptions;
use sqlx::PgPool;
use std::sync::Arc;
use std::time::Instant;

// ─── Data Models ───

#[derive(Serialize, Deserialize, Clone, sqlx::FromRow)]
pub struct PersonData {
    pub id: i32,
    pub name: String,
    pub biohash_template: String,
    pub description: Option<String>,
    pub created_at: Option<chrono::DateTime<Utc>>,
    pub updated_at: Option<chrono::DateTime<Utc>>,
}

/// 用於 GET /api/persons 列表回傳（含摘要資訊）
#[derive(Serialize)]
pub struct PersonSummary {
    pub id: i32,
    pub name: String,
    pub biohash_template: String,
    pub description: Option<String>,
    pub template_bytes: usize,
    pub created_at: Option<String>,
}

#[derive(Deserialize)]
pub struct CreatePersonRequest {
    pub name: String,
    pub biohash_template: String,
    #[serde(default)]
    pub description: Option<String>,
}

#[derive(Deserialize)]
pub struct UpdatePersonRequest {
    pub name: Option<String>,
    pub description: Option<String>,
}

#[derive(Serialize)]
pub struct StatusResponse {
    pub uptime_seconds: u64,
    pub person_count: i64,
    pub db_connected: bool,
    pub version: String,
}

// ─── App State ───

pub struct AppState {
    pub pool: PgPool,
    pub start_time: Instant,
}

// ─── Main ───

#[tokio::main]
async fn main() {
    // 從環境變數讀取資料庫 URL，預設連本地
    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgres://gvw:gvw@localhost/gvw".to_string());

    println!("📡 連接資料庫: {}", database_url);

    let pool = PgPoolOptions::new()
        .max_connections(5)
        .connect(&database_url)
        .await
        .expect("❌ 無法連接 PostgreSQL，請確認資料庫已啟動");

    // 自動建表
    sqlx::query(
        r#"
        CREATE TABLE IF NOT EXISTS persons (
            id              SERIAL PRIMARY KEY,
            name            VARCHAR(255) NOT NULL,
            biohash_template TEXT NOT NULL,
            description     TEXT DEFAULT '',
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        )
        "#,
    )
    .execute(&pool)
    .await
    .expect("❌ 建表失敗");

    println!("✅ 資料庫就緒");

    let state = Arc::new(AppState {
        pool,
        start_time: Instant::now(),
    });

    let app = Router::new()
        .route("/", get(serve_html))
        .route("/api/persons", get(list_persons))
        .route("/api/persons", post(create_person))
        .route("/api/persons/{id}", get(get_person))
        .route("/api/persons/{id}", put(update_person))
        .route("/api/persons/{id}", delete(delete_person))
        .route("/api/status", get(get_status))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
    println!("🚀 伺服器運行中: http://0.0.0.0:3000");

    axum::serve(listener, app).await.unwrap();
}

// ─── Handlers ───

async fn serve_html() -> Html<&'static str> {
    Html(include_str!("../index.html"))
}

async fn list_persons(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let rows = sqlx::query_as::<_, PersonData>(
        "SELECT id, name, biohash_template, description, created_at, updated_at FROM persons ORDER BY id"
    )
    .fetch_all(&state.pool)
    .await;

    match rows {
        Ok(persons) => {
            let summaries: Vec<PersonSummary> = persons
                .into_iter()
                .map(|p| PersonSummary {
                    id: p.id,
                    name: p.name,
                    biohash_template: p.biohash_template.clone(),
                    description: p.description,
                    template_bytes: p.biohash_template.len() / 2,
                    created_at: p.created_at.map(|t| t.to_rfc3339()),
                })
                .collect();
            Json(summaries).into_response()
        }
        Err(e) => {
            eprintln!("❌ 查詢失敗: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn create_person(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<CreatePersonRequest>,
) -> impl IntoResponse {
    if payload.name.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": "名稱不能為空"})),
        )
            .into_response();
    }
    if payload.biohash_template.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": "模板不能為空"})),
        )
            .into_response();
    }

    // 驗證 hex 格式
    if payload.biohash_template.len() % 2 != 0
        || !payload
            .biohash_template
            .chars()
            .all(|c| c.is_ascii_hexdigit())
    {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": "模板必須為有效的 hex 字串"})),
        )
            .into_response();
    }

    let desc = payload.description.unwrap_or_default();

    let result = sqlx::query_as::<_, PersonData>(
        r#"
        INSERT INTO persons (name, biohash_template, description) 
        VALUES ($1, $2, $3) 
        RETURNING id, name, biohash_template, description, created_at, updated_at
        "#,
    )
    .bind(&payload.name)
    .bind(&payload.biohash_template)
    .bind(&desc)
    .fetch_one(&state.pool)
    .await;

    match result {
        Ok(person) => {
            println!(
                "✅ 新增人員: [{}] {} (模板 {} bytes)",
                person.id,
                person.name,
                person.biohash_template.len() / 2
            );
            (StatusCode::CREATED, Json(person)).into_response()
        }
        Err(e) => {
            eprintln!("❌ 新增失敗: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn get_person(
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> impl IntoResponse {
    let result = sqlx::query_as::<_, PersonData>(
        "SELECT id, name, biohash_template, description, created_at, updated_at FROM persons WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&state.pool)
    .await;

    match result {
        Ok(Some(person)) => Json(person).into_response(),
        Ok(None) => StatusCode::NOT_FOUND.into_response(),
        Err(e) => {
            eprintln!("❌ 查詢失敗: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn update_person(
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
    Json(payload): Json<UpdatePersonRequest>,
) -> impl IntoResponse {
    // 先檢查是否存在
    let existing = sqlx::query_as::<_, PersonData>(
        "SELECT id, name, biohash_template, description, created_at, updated_at FROM persons WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&state.pool)
    .await;

    match existing {
        Ok(Some(person)) => {
            let new_name = payload.name.unwrap_or(person.name);
            let new_desc = payload.description.or(person.description);

            let result = sqlx::query_as::<_, PersonData>(
                r#"
                UPDATE persons SET name = $1, description = $2, updated_at = NOW() 
                WHERE id = $3
                RETURNING id, name, biohash_template, description, created_at, updated_at
                "#,
            )
            .bind(&new_name)
            .bind(&new_desc)
            .bind(id)
            .fetch_one(&state.pool)
            .await;

            match result {
                Ok(updated) => Json(updated).into_response(),
                Err(e) => {
                    eprintln!("❌ 更新失敗: {}", e);
                    StatusCode::INTERNAL_SERVER_ERROR.into_response()
                }
            }
        }
        Ok(None) => StatusCode::NOT_FOUND.into_response(),
        Err(e) => {
            eprintln!("❌ 查詢失敗: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn delete_person(
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> impl IntoResponse {
    let result = sqlx::query("DELETE FROM persons WHERE id = $1")
        .bind(id)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) => {
            if r.rows_affected() > 0 {
                println!("🗑️ 刪除人員 ID: {}", id);
                StatusCode::NO_CONTENT
            } else {
                StatusCode::NOT_FOUND
            }
        }
        Err(e) => {
            eprintln!("❌ 刪除失敗: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        }
    }
}

async fn get_status(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let count = sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM persons")
        .fetch_one(&state.pool)
        .await
        .unwrap_or(0);

    let db_ok = sqlx::query("SELECT 1")
        .execute(&state.pool)
        .await
        .is_ok();

    Json(StatusResponse {
        uptime_seconds: state.start_time.elapsed().as_secs(),
        person_count: count,
        db_connected: db_ok,
        version: "0.2.0".to_string(),
    })
}
