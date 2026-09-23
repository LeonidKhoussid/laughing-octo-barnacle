// Backend API types (mirror the FastAPI pydantic schemas).

export interface SpanInfo {
  start: number;
  end: number;
  category: string;
  reason: string;
  rule_id: string;
}

export interface MaskResponse {
  masked_text: string;
  policy_version: string;
  detected_counts: Record<string, number>;
  context_id: string;
  mask_action: string;
  spans: SpanInfo[];
  egress_disabled: boolean;
}

export interface DecisionInfo {
  category: string;
  decision: "MASK" | "KEEP" | "UNCERTAIN";
  rule_id: string;
  detector_id: string;
  evidence_spans: { start: number; end: number }[];
  sensitive_spans: { start: number; end: number }[];
  signals: string[];
}

export interface ContextCheckResponse {
  text: string;
  masked_text: string;
  restored: string;
  exact_round_trip: boolean;
  spans: { start: number; end: number; category: string }[];
  decisions: DecisionInfo[];
  policy_version: string;
}

export interface UnmaskResponse {
  original_text: string;
  policy_version: string;
}

export interface ChatResponse {
  response: string;
  provider: string;
  stub: boolean;
}

export interface RestoreResponseResponse {
  restored_text: string;
  policy_version: string;
}

export interface ConsumerInfo {
  enabled: boolean;
  allow_unmask: boolean;
  allow_llm_egress: boolean;
  mask_action: string;
  detect_types: string;
  authentication: string;
}

export interface ConfigResponse {
  policy_version: string;
  consumers: string[];
  consumer_info: Record<string, ConsumerInfo>;
  detectors: string[];
}

export interface ConfigUpdateResponse {
  policy_version: string;
  consumer: string;
  updated: {
    enabled: boolean;
    allow_unmask: boolean;
    allow_llm_egress: boolean;
    mask_action: string;
    detect_types: string;
  };
}

export interface ApiError {
  status: number;
  code: string;
  message: string;
}

export class ApiErrorImpl extends Error implements ApiError {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
