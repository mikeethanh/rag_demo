"use client";

interface Props {
  questions: string[];
  onSelect: (q: string) => void;
  disabled?: boolean;
}

export function FollowUpChips({ questions, onSelect, disabled }: Props) {
  if (!questions.length) return null;

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        Câu hỏi liên quan
      </p>
      <div className="flex flex-wrap gap-2">
        {questions.map((q, i) => (
          <button
            key={i}
            onClick={() => onSelect(q)}
            disabled={disabled}
            className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40 transition-colors text-left"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
