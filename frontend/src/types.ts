export type AnalystPreview = {
  name: string;
  role: string;
  affiliation: string;
  description: string;
};

export type RetryStage = {
  attempted: number;
  max: number;
};

export type AutoRetryState = {
  running_generation: RetryStage;
  running_feedback: RetryStage;
};

export type RiskSummary = {
  high: number;
  medium: number;
  low: number;
};

export type TokenMetrics = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  llm_calls: number;
  by_node: Record<string, unknown>;
  latency_ms?: number;
  usage_available?: boolean;
};

export type LatencyMetrics = {
  generation_ms: number;
  feedback_ms: number;
  created_to_completed_ms: number;
};

export type TaskMetrics = {
  latency: LatencyMetrics;
  tokens: TokenMetrics;
};

export type Task = {
  id: string;
  type: string;
  company_name: string;
  focus: string;
  target_role: string;
  report_kind: string;
  owner: string;
  assignee: string;
  blocked_by: string[];
  status: string;
  thread_id: string;
  analysts_preview: AnalystPreview[];
  analyst_version: number;
  docx_path: string;
  pdf_path: string;
  error: string;
  failed_stage: string;
  retry_count: number;
  auto_retry: AutoRetryState;
  last_feedback: string;
  risk_summary: RiskSummary;
  final_recommendation: string;
  metrics: TaskMetrics;
  created_at: number;
  updated_at: number;
};

export type TaskEvent = {
  ts: number;
  task_id: string;
  event: string;
  payload: Record<string, unknown>;
};

export type User = {
  username: string;
};
