import type { RunState, SourceDocument } from "../hooks/useRAGAgent";

// Unit-test the state-update logic in isolation by replaying the same
// reducer-style transitions the hook applies inside onEvent/onRunFinalized.
// We import the types and manually apply the same update functions so we
// have deterministic, synchronous tests with no React or fetch involved.

type Patch = { op: string; path: string; value: unknown };

function applyEvent(
  prev: RunState,
  event: { type: string } & Record<string, unknown>,
  ctx: { answerText: string; reasoningText: string; reasoningMsgId: string | null }
): { next: RunState; ctx: typeof ctx } {
  const e = event;
  let next = { ...prev };
  const newCtx = { ...ctx };

  switch (e.type) {
    case "TEXT_MESSAGE_CONTENT":
      newCtx.answerText += (e.delta as string) ?? "";
      next.answerText = newCtx.answerText;
      break;

    case "REASONING_START":
      newCtx.reasoningMsgId = (e.message_id as string) ?? null;
      break;

    case "REASONING_MESSAGE_CONTENT":
      if (e.message_id === newCtx.reasoningMsgId) {
        newCtx.reasoningText += (e.delta as string) ?? "";
        next.reasoningText = newCtx.reasoningText;
      }
      break;

    case "REASONING_END":
      newCtx.reasoningMsgId = null;
      break;

    case "STATE_DELTA": {
      const delta = e.delta as Patch[];
      for (const patch of delta ?? []) {
        if (patch.path === "/source_documents") {
          next.sourceDocuments = (patch.value as SourceDocument[]) ?? [];
        }
        if (patch.path === "/follow_up_questions") {
          next.followUpQuestions = (patch.value as string[]) ?? [];
        }
        if (patch.path === "/branch") {
          next.currentNode = patch.value as string;
          next.isFallback = patch.value === "fallback";
        }
      }
      break;
    }

    case "RUN_ERROR":
      next.status = "error";
      next.errorMessage = (e.message as string) ?? "Unknown error";
      break;
  }

  return { next, ctx: newCtx };
}

const blank: RunState = {
  status: "running",
  answerText: "",
  reasoningText: "",
  sourceDocuments: [],
  followUpQuestions: [],
  errorMessage: null,
  currentNode: null,
  isFallback: false,
};

const emptyCtx = { answerText: "", reasoningText: "", reasoningMsgId: null };

describe("useRAGAgent state logic", () => {
  test("TEXT_MESSAGE_CONTENT accumulates answerText", () => {
    let { next, ctx } = applyEvent(blank, { type: "TEXT_MESSAGE_CONTENT", delta: "Paris " }, emptyCtx);
    ({ next, ctx } = applyEvent(next, { type: "TEXT_MESSAGE_CONTENT", delta: "is great." }, ctx));
    expect(next.answerText).toBe("Paris is great.");
  });

  test("REASONING_START/CONTENT/END tracks reasoning text", () => {
    let { next, ctx } = applyEvent(blank, { type: "REASONING_START", message_id: "m1" }, emptyCtx);
    ({ next, ctx } = applyEvent(next, { type: "REASONING_MESSAGE_CONTENT", message_id: "m1", delta: "thinking " }, ctx));
    ({ next, ctx } = applyEvent(next, { type: "REASONING_MESSAGE_CONTENT", message_id: "m1", delta: "hard" }, ctx));
    ({ next, ctx } = applyEvent(next, { type: "REASONING_END", message_id: "m1" }, ctx));
    expect(next.reasoningText).toBe("thinking hard");
    expect(ctx.reasoningMsgId).toBeNull();
  });

  test("REASONING_MESSAGE_CONTENT ignores wrong message_id", () => {
    const ctx0 = { ...emptyCtx, reasoningMsgId: "m1" };
    const { next } = applyEvent(blank, { type: "REASONING_MESSAGE_CONTENT", message_id: "other", delta: "ignored" }, ctx0);
    expect(next.reasoningText).toBe("");
  });

  test("STATE_DELTA sets source_documents", () => {
    const docs = [{ content: "text", source: "Luật", page: "" }];
    const { next } = applyEvent(blank, {
      type: "STATE_DELTA",
      delta: [{ op: "replace", path: "/source_documents", value: docs }],
    }, emptyCtx);
    expect(next.sourceDocuments).toEqual(docs);
  });

  test("STATE_DELTA sets follow_up_questions", () => {
    const { next } = applyEvent(blank, {
      type: "STATE_DELTA",
      delta: [{ op: "replace", path: "/follow_up_questions", value: ["q1", "q2"] }],
    }, emptyCtx);
    expect(next.followUpQuestions).toEqual(["q1", "q2"]);
  });

  test("STATE_DELTA branch=fallback sets isFallback", () => {
    const { next } = applyEvent(blank, {
      type: "STATE_DELTA",
      delta: [{ op: "replace", path: "/branch", value: "fallback" }],
    }, emptyCtx);
    expect(next.isFallback).toBe(true);
    expect(next.currentNode).toBe("fallback");
  });

  test("STATE_DELTA branch=legal does not set isFallback", () => {
    const { next } = applyEvent(blank, {
      type: "STATE_DELTA",
      delta: [{ op: "replace", path: "/branch", value: "legal" }],
    }, emptyCtx);
    expect(next.isFallback).toBe(false);
    expect(next.currentNode).toBe("legal");
  });

  test("RUN_ERROR sets status=error and errorMessage", () => {
    const { next } = applyEvent(blank, { type: "RUN_ERROR", message: "LLM fail" }, emptyCtx);
    expect(next.status).toBe("error");
    expect(next.errorMessage).toBe("LLM fail");
  });
});
