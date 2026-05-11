"use client";

import { useState } from "react";

interface Props {
  text: string;
}

export function CoTPanel({ text }: Props) {
  const [open, setOpen] = useState(false);

  if (!text) return null;

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 text-sm">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-2 font-medium text-amber-800 hover:bg-amber-100 rounded-xl transition-colors"
      >
        <span>💭 Suy luận nội bộ</span>
        <span className="text-xs text-amber-500">{open ? "▲ Thu gọn" : "▼ Mở rộng"}</span>
      </button>

      {open && (
        <div className="border-t border-amber-200 px-4 py-3 font-mono text-xs text-amber-700 whitespace-pre-wrap leading-relaxed">
          {text}
        </div>
      )}
    </div>
  );
}
