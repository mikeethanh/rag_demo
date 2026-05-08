# System Architecture Diagrams

Thư mục này chứa các sơ đồ kiến trúc cho hệ thống Agentic RAG. Mỗi diagram có 2 file: `.mmd` (nguồn Mermaid) và `.png` (ảnh export).

---

## 1. Agentic RAG — Tổng quan hệ thống

**Files:** `agentic_rag_architecture.mmd` / `agentic_rag_architecture.png`

Sơ đồ mô tả toàn bộ luồng xử lý từ khi nhận User Request đến khi trả về phản hồi cuối cùng.

### Workflow

```
User Request → Semantic Router → [4 nhánh] → Response Aggregator → User
```

**Semantic Router** phân loại câu hỏi đầu vào thành 4 nhánh:

| Nhánh | Loại query | Xử lý |
|---|---|---|
| 1 | General | Gọi thẳng LLM API (`gpt-4o-mini`), không cần retrieval |
| 2 | Legal | Chuyển vào LangGraph RAG Subgraph (xem diagram 2) |
| 3 | Calculation | Kích hoạt Function Calling — gọi tuần tự Tool 1 → Tool 2 |
| 4 | Ambiguous | Web Search Agent rewrite query → search → rank → summarize |

**Response Aggregator** là điểm hội tụ cuối: chuẩn hóa output từ cả 4 nhánh về cùng một schema trước khi ghi vào MariaDB và trả về UI.

---

## 2. Legal RAG — LangGraph Module

**Files:** `legal_rag_langgraph.mmd` / `legal_rag_langgraph.png`

Sơ đồ chi tiết cho nhánh Legal, được implement bằng LangGraph với `GraphState` và các conditional edges.

### GraphState

```python
class GraphState(TypedDict):
    query: str                   # câu hỏi gốc / đã rewrite
    documents: list[Document]    # chunks lấy từ Qdrant
    generation: str              # câu trả lời được sinh ra
    transformation_count: int    # số lần rewrite đã thực hiện
```

### Workflow — Node theo thứ tự

```
START → Rewrite → Retrieve → Grade_Docs → Generate → Follow_up → END
```

#### Node 1 — Rewrite
Áp dụng **Chain-of-Thought (CoT) prompting** để viết lại câu hỏi pháp lý gốc nhằm tối ưu cho retrieval. Mỗi lần rewrite tăng `transformation_count` lên 1.

#### Node 2 — Retrieve
Embed câu hỏi đã rewrite bằng `text-embedding-3-large`, tìm kiếm top-5 chunks trong Qdrant collection `llm` theo DOT similarity.

#### Node 3 — Grade_Docs *(Self-Reflection)*
LLM tự chấm điểm độ liên quan (0–1) cho từng document so với câu hỏi.

**Conditional edge — Rollback Guard:**
- `avg_score ≥ 0.7` → tiếp tục sang **Generate**
- `avg_score < 0.7` và `transformation_count < 3` → **Rollback** về **Rewrite**
- `avg_score < 0.7` và `transformation_count ≥ 3` → **Fallback Node** (trả lời không có citation, tránh vòng lặp vô hạn)

#### Node 4 — Generate
Xây dựng prompt đầy đủ (system + history + documents + query) với CoT instruction yêu cầu LLM trích dẫn chỉ số document trước khi đưa ra câu trả lời.

#### Node 5 — Follow_up
Dựa trên `query + documents + generation`, sinh 3 câu hỏi follow-up phù hợp ngữ cảnh để hỗ trợ người dùng tiếp tục tra cứu pháp lý.

### Luồng dữ liệu ra (Response Aggregator nhận)

| Field | Nguồn |
|---|---|
| `generation` | Node Generate (có citation) |
| `source_documents` | Node Retrieve (title, source, page) |
| `follow_up_questions` | Node Follow_up |
