# Legal RAG LangGraph + AG-UI — Giải thích Workflow

---

## 1. Legal RAG LangGraph Workflow

### Tổng quan

Pipeline RAG dành cho truy vấn pháp lý, được xây dựng trên LangGraph. Toàn bộ trạng thái được lưu trong `GraphState` và luồng xử lý đi qua 5 node chính, có cơ chế tự phục hồi khi chất lượng tài liệu kém.

### GraphState — Shared State

| Field | Type | Ý nghĩa |
|---|---|---|
| `query` | str | Câu hỏi gốc / đã được rewrite |
| `documents` | list | Danh sách chunks tìm được từ Qdrant |
| `generation` | str | Câu trả lời do LLM sinh ra |
| `transformation_count` | int | Số lần đã rewrite query (chống vòng lặp vô hạn) |

---

### Các Node

#### Node: Rewrite
- Nhận câu hỏi pháp lý thô từ user.
- Dùng **Chain-of-Thought (CoT) Prompt**: *"Think step-by-step about the legal intent of the query..."*
- LLM rewrites query để tối ưu cho semantic search.
- Tăng `transformation_count += 1`.

#### Node: Retrieve
- Embed query đã rewrite bằng `text-embedding-3-large`.
- Tìm kiếm Qdrant collection `"llm"`, lấy **top-k=6** chunks liên quan nhất.
- Trả về danh sách `documents` với metadata: `{title, source, page}`.

#### Node: Grade_Docs (Self-Reflection)
- Với **mỗi document**, LLM tự chấm điểm mức độ liên quan từ **0–1** so với query đã rewrite.
- Tính `avg_score`.
- Nếu `avg_score >= 0.7` → **Pass** → sang Generate.
- Nếu `avg_score < 0.7` → **Rollback** → quay lại Rewrite.

#### Conditional Edge: Rollback Guard
- Kiểm tra `transformation_count < 3`:
  - **Yes** → ROLLBACK: quay lại Node Rewrite với query mới.
  - **No** → ABORT: chuyển sang **Fallback Node** (trả lời từ LLM nội bộ, không có citations).

#### Node: Generate
- Build prompt: `system + history + docs + query`.
- CoT instruction: *"Reason through statutes, cite doc indices, then answer"*.
- LLM sinh `generation: str` — câu trả lời có trích dẫn điều luật cụ thể.

#### Node: Follow_up
- Nhận context = `query + documents + generation`.
- LLM sinh **3 câu hỏi follow-up** phù hợp với ngữ cảnh pháp lý.
- Đính kèm vào response payload.

#### Response Aggregator
- Gộp kết quả thành payload cuối:
  - `generation` — câu trả lời có trích dẫn (cited answer)
  - `source documents + pages` — nguồn tài liệu gốc
  - `follow_up_questions` — gợi ý câu hỏi tiếp theo

→ **END**: Trả về Semantic Router.

---

### Luồng xử lý đầy đủ

```
START (User Legal Query)
  │
  ▼
Node: Rewrite  ──────────────────────────────────────────────┐
  │                                                           │ (rollback nếu avg_score < 0.7
  ▼                                                           │  và transformation_count < 3)
Node: Retrieve                                                │
  │                                                           │
  ▼                                                           │
Node: Grade_Docs ──[fail]──► Conditional Edge: Rollback Guard─┘
  │ [pass]                          │ [abort nếu count >= 3]
  │                                 ▼
  │                          Fallback Node (LLM only)
  ▼
Node: Generate
  │
  ▼
Node: Follow_up
  │
  ▼
Response Aggregator
  │
  ▼
END → Return to Semantic Router
```

---

## 2. AG-UI Workflow

### Tổng quan

AG-UI là giao thức kết nối **LangGraph Agent** với **Frontend** thông qua **Shared State** dùng event streaming. Mục tiêu: hiển thị real-time từng bước xử lý của agent lên UI mà không cần polling.

---

### 3 thành phần chính

#### LangGraph Agent (backend)
Chạy tuần tự các node: `Rewrite → Retrieve → Grade_Docs → Generate → Follow_up`.

#### Shared State (trung gian)
Lưu trữ toàn bộ trạng thái của agent:
- `query: str`
- `documents: list`
- `generation: str`
- `transformation_count: int`
- `follow_up_questions: list`

Agent **write/patch** vào Shared State sau mỗi bước. Frontend **read** từ Shared State.

#### AG-UI Frontend
Render kết quả theo từng event nhận được:
- **Agent Step Indicator** — hiển thị node nào đang chạy
- **CoT Reasoning Panel** — hiển thị quá trình reasoning của LLM
- **Answer + Citations** — hiển thị câu trả lời cuối + nguồn trích dẫn
- **Follow-up Questions** — clickable chips để user tiếp tục hỏi

---

### Event Protocol (Agent → Shared State → Frontend)

| Event | Payload | Frontend phản ứng |
|---|---|---|
| `RUN_STARTED` | `{ threadId, runId }` | Hiển thị spinner / bắt đầu session |
| `STATE_SNAPSHOT` | `{ snapshot: full state }` | Cập nhật toàn bộ state lần đầu |
| `REASONING_START` | — | Mở CoT Reasoning Panel |
| `REASONING_MESSAGE_CONTENT` ×N | Từng token reasoning | Stream text vào CoT Panel |
| `REASONING_END` | — | Đóng CoT Panel |
| `STATE_DELTA` | `{ delta: RFC-6902 patch }` | Patch state từng phần (incremental update) |
| `TEXT_MESSAGE_START` | — | Bắt đầu stream câu trả lời |
| `TEXT_MESSAGE_CONTENT` ×N | Từng token answer | Stream vào Answer + Citations box |
| `TEXT_MESSAGE_END` | — | Kết thúc stream |
| `RUN_FINISHED` | `{ outcome: success }` | Hiển thị follow-up chips, kết thúc session |

> `STATE_DELTA` dùng **RFC-6902 JSON Patch** — chỉ gửi phần thay đổi, không gửi lại toàn bộ state → tiết kiệm băng thông.

---

### Luồng tương tác đầy đủ

```
User → initial query
  │
  ▼
LangGraph Agent bắt đầu chạy
  │  ┌─────────────────────────────────────────────────────────┐
  │  │ Shared State                                             │
  │  │  write ◄── Agent (sau mỗi node)                         │
  │  │  read  ──► Frontend (theo event stream)                  │
  │  └─────────────────────────────────────────────────────────┘
  │
  ├─ RUN_STARTED ──────────────────────────► Agent Step Indicator
  ├─ REASONING_START/CONTENT/END ──────────► CoT Reasoning Panel
  ├─ STATE_DELTA (patch) ──────────────────► cập nhật state
  ├─ TEXT_MESSAGE_START/CONTENT/END ───────► Answer + Citations
  └─ RUN_FINISHED ─────────────────────────► Follow-up Questions (chips)

User click follow-up chip
  │
  └─ HTTP POST /runs { input: follow_up }
       │
       └─ Agent chạy lại từ đầu với query mới
```

---

## 3. Tổng hợp — Hai workflow kết hợp

```
User gõ câu hỏi pháp lý
        │
        ▼
  AG-UI Frontend (gửi HTTP POST /runs)
        │
        ▼
  LangGraph Agent (Legal RAG Pipeline)
    Rewrite → Retrieve → Grade_Docs → Generate → Follow_up
        │
        │  (stream events qua Shared State)
        ▼
  AG-UI Frontend render real-time:
    - Bước đang chạy (step indicator)
    - CoT reasoning của LLM
    - Câu trả lời + trích dẫn điều luật
    - Gợi ý câu hỏi tiếp theo (clickable)
        │
        ▼
  User click follow-up → vòng lặp mới
```

**Điểm mấu chốt:**
- **LangGraph** quản lý logic RAG + self-reflection (Rollback Guard).
- **AG-UI** quản lý giao tiếp real-time giữa agent và UI qua event streaming + RFC-6902 patch.
- **Shared State** là trung gian duy nhất — agent write, frontend read, không cần polling.
