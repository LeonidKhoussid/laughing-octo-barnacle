// Small API client matching the actual FastAPI backend.
import type {
  ApiError,
  ChatResponse,
  ConfigResponse,
  ConfigUpdateResponse,
  ContextCheckResponse,
  MaskResponse,
  RestoreResponseResponse,
  UnmaskResponse,
} from "./types";
import { ApiErrorImpl } from "./types";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  let data: unknown = null;
  try {
    data = await resp.json();
  } catch {
    data = null;
  }
  if (!resp.ok) {
    const detail = (data as { detail?: { code?: string; message?: string } })?.detail;
    throw new ApiErrorImpl(
      resp.status,
      detail?.code || "error",
      detail?.message || `HTTP ${resp.status}`,
    );
  }
  return data as T;
}

export interface MaskParams {
  text: string;
  consumer: string;
  apiKey?: string;
}

export interface ChatParams {
  maskedText: string;
  consumer: string;
  contextId: string;
  apiKey?: string;
}

export interface UnmaskParams {
  maskedText: string;
  consumer: string;
  contextId: string;
  apiKey?: string;
}

export interface RestoreResponseParams {
  responseText: string;
  consumer: string;
  contextId: string;
  apiKey?: string;
}

function authHeaders(apiKey?: string): Record<string, string> {
  return apiKey ? { "X-API-Key": apiKey } : {};
}

export const api = {
  getConfig: () => request<ConfigResponse>("/config"),

  checkContext: (text: string) =>
    request<ContextCheckResponse>("/trust-lab/action-a", {
      method: "POST",
      body: JSON.stringify({ text, consumer: "autocheck", labeled: false, run_variations: false }),
    }),

  mask: (p: MaskParams) =>
    request<MaskResponse>("/demo/mask", {
      method: "POST",
      headers: authHeaders(p.apiKey),
      body: JSON.stringify({ text: p.text, consumer: p.consumer }),
    }),

  chat: (p: ChatParams) =>
    request<ChatResponse>("/demo/chat", {
      method: "POST",
      headers: authHeaders(p.apiKey),
      body: JSON.stringify({
        masked_text: p.maskedText,
        consumer: p.consumer,
        context_id: p.contextId,
      }),
    }),

  unmask: (p: UnmaskParams) =>
    request<UnmaskResponse>("/demo/unmask", {
      method: "POST",
      headers: authHeaders(p.apiKey),
      body: JSON.stringify({
        masked_text: p.maskedText,
        consumer: p.consumer,
        context_id: p.contextId,
      }),
    }),

  restoreResponse: (p: RestoreResponseParams) =>
    request<RestoreResponseResponse>("/demo/restore-response", {
      method: "POST",
      headers: authHeaders(p.apiKey),
      body: JSON.stringify({
        response_text: p.responseText,
        consumer: p.consumer,
        context_id: p.contextId,
      }),
    }),

  updateConfig: (consumer: string, updates: Record<string, unknown>, adminKey: string) =>
    request<ConfigUpdateResponse>("/config/update", {
      method: "POST",
      body: JSON.stringify({ consumer, updates, admin_key: adminKey }),
    }),
};

export type { ApiError };
