use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{Html, IntoResponse},
    routing::{delete, get, post, put},
    Json, Router,
};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::sync::{Arc, RwLock};
use uuid::Uuid;

// 1. 定義資料結構
#[derive(Serialize, Deserialize, Clone)]
pub struct PersonData {
    pub id: String,
    pub vector: Vec<f32>,
    pub is_unlocked: bool,
    pub name: Option<String>,
    pub description: Option<String>,
    pub created_at: Option<String>,
}

// 接收前端傳來的新增請求
#[derive(Deserialize)]
pub struct CreateRequest {
    pub vector: Vec<f32>,
}

// 記憶體內的資料庫狀態 (使用 Arc 和 RwLock 確保執行緒安全)
type AppState = Arc<RwLock<Vec<PersonData>>>;

#[tokio::main]
async fn main() {
    // 初始化空資料庫
    let state: AppState = Arc::new(RwLock::new(Vec::new()));

    // 設定路由
let app = Router::new()
        .route("/", get(serve_html))
        .route("/api/data", get(list_data))
        .route("/api/data", post(create_data))
        .route("/api/data/{id}/unlock", put(unlock_data))
        .route("/api/data/{id}", delete(delete_data)) 
        .with_state(state);

    // 綁定 Port 3000
    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
    println!("伺服器運行中： http://localhost:3000");
    
    axum::serve(listener, app).await.unwrap();
}

// 2. API 處理函式

// 回傳前端 HTML 頁面
async fn serve_html() -> Html<&'static str> {
    Html(include_str!("../index.html"))
}

// 取得所有資料
async fn list_data(State(state): State<AppState>) -> impl IntoResponse {
    let db = state.read().unwrap();
    Json(db.clone())
}

// 新增「未解鎖」的向量資料
async fn create_data(
    State(state): State<AppState>,
    Json(payload): Json<CreateRequest>,
) -> impl IntoResponse {
    let new_item = PersonData {
        id: Uuid::new_v4().to_string(),
        vector: payload.vector,
        is_unlocked: false,
        name: None,
        description: None,
        created_at: None,
    };

    state.write().unwrap().push(new_item.clone());
    (StatusCode::CREATED, Json(new_item))
}

// 解鎖並補齊資料 (替換 JSON)
async fn unlock_data(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let mut db = state.write().unwrap();
    
    if let Some(item) = db.iter_mut().find(|i| i.id == id) {
        item.is_unlocked = true;
        item.name = Some("樹莓派特務 007".to_string());
        item.description = Some("來自邊緣運算設備的向量測試實體".to_string());
        item.created_at = Some(Utc::now().to_rfc3339());
        
        // 將向量擴充或截斷至 512 維 (這裡以 0.0 填充)
        item.vector.resize(512, 0.0);
        
        return (StatusCode::OK, Json(item.clone())).into_response();
    }
    StatusCode::NOT_FOUND.into_response()
}

// 刪除資料
async fn delete_data(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let mut db = state.write().unwrap();
    let original_len = db.len();
    db.retain(|item| item.id != id);

    if db.len() < original_len {
        StatusCode::NO_CONTENT
    } else {
        StatusCode::NOT_FOUND
    }
}
