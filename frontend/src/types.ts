export type AnalystPreview = {
  name: string;
  role: string;
  affiliation: string;
  description: string;
};

export type RiskSummary = {
  high: number;
  medium: number;
  low: number;
};

export type Task = {
  id: string;
  task_type: string;
  owner: string;
  company_name: string;
  focus: string;
  target_role: string;
  industry_pack: string;
  max_analysts: number;
  status: string;
  thread_id: string;
  analysts_preview: AnalystPreview[];
  analyst_version: number;
  docx_path: string;
  pdf_path: string;
  error: string;
  failed_stage: string;
  last_feedback: string;
  risk_summary: RiskSummary;
  final_recommendation: string;
  report_review_status: string;
  report_review_summary: string;
  created_at: number;
  updated_at: number;
};

export type TaskEvent = {
  ts: number;
  task_id: string;
  event: string;
  payload: Record<string, unknown>;
};

export type TaskMetrics = {
  call_count: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_latency_ms: number;
  estimated_cost_usd: number;
  over_budget: boolean;
  by_node: Record<string, {
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    total_latency_ms: number;
    estimated_cost: number;
  }>;
};

export type User = {
  username: string;
};
