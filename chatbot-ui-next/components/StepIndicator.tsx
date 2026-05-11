"use client";

const NODE_LABELS: Record<string, string> = {
  classify: "Phân loại câu hỏi",
  rewrite: "Cải thiện câu hỏi",
  retrieve: "Tìm kiếm tài liệu",
  grade_docs: "Đánh giá tài liệu",
  generate: "Tạo câu trả lời",
  fallback: "Trả lời dự phòng",
  follow_up: "Câu hỏi liên quan",
  general_answer: "Đang trả lời",
  calculation_answer: "Tính toán hình phạt",
  web_search_answer: "Tìm kiếm web",
};

const NODE_ORDER = [
  "classify",
  "rewrite",
  "retrieve",
  "grade_docs",
  "generate",
  "fallback",
  "follow_up",
  "general_answer",
  "calculation_answer",
  "web_search_answer",
];

interface Props {
  currentNode: string | null;
  status: "idle" | "running" | "done" | "error";
}

export function StepIndicator({ currentNode, status }: Props) {
  if (status === "idle") return null;

  const label =
    currentNode && NODE_LABELS[currentNode]
      ? NODE_LABELS[currentNode]
      : currentNode ?? "";

  return (
    <div className="flex items-center gap-2 text-sm text-gray-500">
      {status === "running" && (
        <span className="inline-block h-3 w-3 rounded-full bg-blue-400 animate-pulse" />
      )}
      {status === "done" && (
        <span className="inline-block h-3 w-3 rounded-full bg-green-500" />
      )}
      {status === "error" && (
        <span className="inline-block h-3 w-3 rounded-full bg-red-500" />
      )}
      <span>
        {status === "running" && label ? `${label}…` : ""}
        {status === "done" ? "Hoàn thành" : ""}
        {status === "error" ? "Lỗi" : ""}
      </span>
    </div>
  );
}
