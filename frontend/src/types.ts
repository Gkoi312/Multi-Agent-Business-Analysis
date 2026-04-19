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
  company_name: string;
  focus: string;
  target_role: string;
  industry_pack: string;
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
