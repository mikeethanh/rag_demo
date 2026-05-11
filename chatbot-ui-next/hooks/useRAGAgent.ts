"use client";

import { useCallback, useRef, useState } from "react";
import { HttpAgent } from "@ag-ui/client";
import type { BaseEvent } from "@ag-ui/core";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface SourceDocument {
  title?: string;
  content?: string;
  source: string;
  page: string;
}

export interface RunState {
  status: "idle" | "running" | "done" | "error";
  answerText: string;
  reasoningText: string;
  sourceDocuments: SourceDocument[];
  followUpQuestions: string[];
  errorMessage: string | null;
  currentNode: string | null;
  isFallback: boolean;
}

const initialState: RunState = {
  status: "idle",
  answerText: "",
  reasoningText: "",
  sourceDocuments: [],
  followUpQuestions: [],
  errorMessage: null,
  currentNode: null,
  isFallback: false,
};

export function useRAGAgent(threadId: string) {
  const [state, setState] = useState<RunState>(initialState);
  const agentRef = useRef<HttpAgent | null>(null);

  if (!agentRef.current) {
    agentRef.current = new HttpAgent({
      url: `${API_BASE}/runs`,
      threadId,
    });
  }

  const sendMessage = useCallback(async (content: string) => {
    const agent = agentRef.current!;

    setState({ ...initialState, status: "running" });
    agent.addMessage({ id: crypto.randomUUID(), role: "user", content });

    let answerText = "";
    let reasoningText = "";
    let reasoningMsgId: string | null = null;

    try {
      await agent.runAgent(undefined, {
        onEvent({ event }: { event: BaseEvent }) {
          const e = event as BaseEvent & Record<string, unknown>;
          switch (e.type) {
            case "TEXT_MESSAGE_CONTENT":
              answerText += (e.delta as string) ?? "";
              setState((prev) => ({ ...prev, answerText }));
              break;

            case "REASONING_START":
              // AG-UI serializes as camelCase: messageId (not message_id)
              reasoningMsgId = (e.messageId as string) ?? null;
              break;

            case "REASONING_MESSAGE_CONTENT":
              if (e.messageId === reasoningMsgId) {
                reasoningText += (e.delta as string) ?? "";
                setState((prev) => ({ ...prev, reasoningText }));
              }
              break;

            case "REASONING_END":
              reasoningMsgId = null;
              break;

            case "STEP_STARTED":
              setState((prev) => ({
                ...prev,
                currentNode: (e.stepName as string) ?? null,
              }));
              break;

            case "STEP_FINISHED":
              // Keep currentNode visible until next step starts
              break;

            case "STATE_SNAPSHOT": {
              const snapshot = e.snapshot as Record<string, unknown> | null;
              if (!snapshot) break;
              if (snapshot.source_documents !== undefined) {
                setState((prev) => ({
                  ...prev,
                  sourceDocuments: (snapshot.source_documents as SourceDocument[]) ?? [],
                }));
              }
              if (snapshot.follow_up_questions !== undefined) {
                setState((prev) => ({
                  ...prev,
                  followUpQuestions: (snapshot.follow_up_questions as string[]) ?? [],
                }));
              }
              if (snapshot.branch !== undefined) {
                setState((prev) => ({
                  ...prev,
                  isFallback: snapshot.branch === "fallback",
                }));
              }
              break;
            }

            case "RUN_ERROR":
              setState((prev) => ({
                ...prev,
                status: "error",
                errorMessage: (e.message as string) ?? "Unknown error",
              }));
              break;
          }
        },
        onRunFinalized() {
          setState((prev) =>
            prev.status === "error" ? prev : { ...prev, status: "done", currentNode: null }
          );
        },
        onRunFailed({ error }: { error: Error }) {
          setState((prev) => ({
            ...prev,
            status: "error",
            errorMessage: error.message,
          }));
        },
      });
    } catch (err) {
      setState((prev) => ({
        ...prev,
        status: "error",
        errorMessage: String(err),
      }));
    }
  }, []);

  const reset = useCallback(() => {
    setState(initialState);
  }, []);

  return { state, sendMessage, reset };
}
