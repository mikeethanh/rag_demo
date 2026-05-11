"use client";

import { useEffect, useRef, useState } from "react";
import { useRAGAgent } from "@/hooks/useRAGAgent";
import { ChatInput } from "./ChatInput";
import { StepIndicator } from "./StepIndicator";
import { CoTPanel } from "./CoTPanel";
import { AnswerBox } from "./AnswerBox";
import { Citations } from "./Citations";
import { FollowUpChips } from "./FollowUpChips";

function getOrCreateThreadId(): string {
  if (typeof window === "undefined") return "default";
  const stored = localStorage.getItem("rag_thread_id");
  if (stored) return stored;
  const id = crypto.randomUUID();
  localStorage.setItem("rag_thread_id", id);
  return id;
}

export function ChatPanel() {
  const [threadId] = useState<string>(() => getOrCreateThreadId());
  const { state, sendMessage } = useRAGAgent(threadId);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [state.answerText, state.reasoningText]);

  const isRunning = state.status === "running";

  return (
    <div className="flex-1 flex flex-col">
      <main className="flex-1 px-4 py-8 flex flex-col gap-6 overflow-y-auto">
        {state.status === "idle" && (
          <div className="text-center text-gray-400 text-sm mt-16">
            <div className="text-4xl mb-4">⚖️</div>
            <p>Hỏi bất kỳ câu hỏi nào về luật giao thông đường bộ.</p>
          </div>
        )}

        <StepIndicator currentNode={state.currentNode} status={state.status} />

        {state.status === "error" && state.errorMessage && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            ⚠️ {state.errorMessage}
          </div>
        )}

        <CoTPanel text={state.reasoningText} />

        <AnswerBox
          text={state.answerText}
          isStreaming={isRunning}
          isFallback={state.isFallback}
        />

        {state.status === "done" && <Citations docs={state.sourceDocuments} />}

        {state.status === "done" && (
          <FollowUpChips
            questions={state.followUpQuestions}
            onSelect={(q) => sendMessage(q)}
            disabled={isRunning}
          />
        )}

        <div ref={bottomRef} />
      </main>

      <div className="sticky bottom-0 bg-white border-t border-gray-200 px-4 py-4 shadow-sm">
        <ChatInput onSend={sendMessage} disabled={isRunning} />
      </div>
    </div>
  );
}
