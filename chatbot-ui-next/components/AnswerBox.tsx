"use client";

interface Props {
  text: string;
  isStreaming: boolean;
  isFallback: boolean;
}

export function AnswerBox({ text, isStreaming, isFallback }: Props) {
  if (!text && !isStreaming) return null;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
          Trả lời
        </span>
        {isFallback && (
          <span className="rounded-full bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-700">
            Dự phòng
          </span>
        )}
        {isStreaming && (
          <span className="h-2 w-2 rounded-full bg-blue-400 animate-pulse" />
        )}
      </div>

      <div className="text-sm leading-relaxed text-gray-800 whitespace-pre-wrap">
        {text}
        {isStreaming && (
          <span className="inline-block w-0.5 h-4 bg-blue-500 animate-pulse ml-0.5 align-text-bottom" />
        )}
      </div>
    </div>
  );
}
