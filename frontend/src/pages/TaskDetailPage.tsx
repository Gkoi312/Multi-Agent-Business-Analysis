import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task, TaskMetrics } from "../types";

const ACTIVE_STATUSES = new Set(["pending", "running_generation", "running_feedback"]);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getStatusLabel(status: string) {
  const statusLabels: Record<string, string> = {
    pending: "待处理",
    running_generation: "生成中",
    awaiting_feedback: "等待反馈",
    running_feedback: "处理反馈中",
    failed: "失败",
    completed: "已完成",
  };
  return statusLabels[status] ?? status;
}

function getTaskTypeLabel(taskType: string) {
  const labels: Record<string, string> = {
    due_diligence: "AI 科技公司调研",
  };
  return labels[taskType] ?? taskType;
}

/** Map status + metrics into a readable pipeline stage description. */
function getProgressDescription(status: string, metrics: TaskMetrics | null): string {
  if (status === "pending") return "正在创建任务…";
  if (status === "running_generation") return "正在分析公司类型、加载技能库、生成分析师…";
  if (status === "awaiting_feedback") return "请审核分析师阵容，提交反馈后继续。";
  if (status === "running_feedback") {
    if (!metrics) return "正在启动调研流水线…";
    const nodes = Object.keys(metrics.by_node);
    const hasPlan = nodes.some((n) => n.includes("plan"));
    const hasInterview = nodes.some((n) => n.includes("interview") || n.includes("conduct"));
    const hasWrite = nodes.some((n) => n.includes("write"));
    const hasReview = nodes.some((n) => n.includes("review") || n.includes("finalize"));
    if (hasReview) return "正在审核最终报告…";
    if (hasWrite) return "正在撰写调研报告…";
    if (hasInterview) return `正在进行专家访谈（已调用 ${metrics.call_count} 次 LLM）…`;
    if (hasPlan) return "正在执行调研计划…";
    return "正在准备调研…";
  }
  if (status === "completed") return "报告已生成。";
  if (status === "failed") return "任务失败。";
  return "";
}

/** Which pipeline step index is currently active. */
function activeStepIndex(status: string): number {
  if (status === "pending" || status === "running_generation") return 0;
  if (status === "awaiting_feedback") return 1;
  if (status === "running_feedback") return 2;
  if (status === "completed") return 3;
  return -1;
}

/** Format seconds into m:ss or h:mm:ss. */
function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (h > 0) return `${h}:${pad(m)}:${pad(sec)}`;
  return `${m}:${pad(sec)}`;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const PIPELINE_STEPS = [
  { label: "生成分析师", icon: "🧠" },
  { label: "等待反馈", icon: "💬" },
  { label: "执行调研", icon: "🔍" },
  { label: "完成", icon: "✅" },
];

function PipelineStepper({ status }: { status: string }) {
  const active = activeStepIndex(status);

  return (
    <div className="pipeline-stepper">
      {PIPELINE_STEPS.map((step, i) => {
        let cls = "pipeline-step";
        if (i < active) cls += " is-done";
        else if (i === active) cls += " is-active";
        return (
          <div className={cls} key={step.label}>
            <span className="pipeline-step-icon">{step.icon}</span>
            <span className="pipeline-step-label">{step.label}</span>
          </div>
        );
      })}
    </div>
  );
}

function AnalystSkeleton({ count }: { count: number }) {
  return (
    <div className="task-grid">
      {Array.from({ length: count }).map((_, i) => (
        <article className="panel nested-panel skeleton-card" key={i}>
          <div className="skeleton-line skeleton-title" />
          <div className="skeleton-line skeleton-text" />
          <div className="skeleton-line skeleton-text short" />
          <div className="skeleton-line skeleton-text" />
        </article>
      ))}
    </div>
  );
}

function ElapsedBadge({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const tick = () => setElapsed((Date.now() / 1000) - startedAt);
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  return (
    <span className="elapsed-badge">
      {formatElapsed(elapsed)}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function TaskDetailPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { taskId = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [metrics, setMetrics] = useState<TaskMetrics | null>(null);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const locationState = location.state as
    | { fromTasks?: boolean; returnTo?: string; returnLabel?: string }
    | null;

  const taskRef = useRef<Task | null>(null);
  taskRef.current = task;

  const isRunning = task ? ACTIVE_STATUSES.has(task.status) : false;

  useEffect(() => {
    if (!taskId) {
      setLoading(false);
      setError("缺少任务 ID。");
      return undefined;
    }

    let cancelled = false;

    const load = async () => {
      try {
        const [nextTask, nextMetrics] = await Promise.all([
          api.getTask(taskId),
          api.getTaskMetrics(taskId),
        ]);
        if (!cancelled) {
          setTask(nextTask);
          if (nextMetrics) setMetrics(nextMetrics);
          setError("");
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "加载任务失败");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    setLoading(true);
    void load();
    const interval = window.setInterval(() => {
      const current = taskRef.current;
      if (current && !ACTIVE_STATUSES.has(current.status)) {
        return;
      }
      void load();
    }, 2500);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [taskId]);

  const handleFeedbackSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!task) return;
    setSubmitting(true);
    try {
      const { task: updatedTask } = await api.submitFeedback(task.id, { feedback });
      setTask(updatedTask);
      setFeedback("");
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "提交反馈失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = async () => {
    if (!task) return;
    setSubmitting(true);
    try {
      await api.retryTask(task.id);
      const refreshed = await api.getTask(task.id);
      setTask(refreshed);
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "重试失败");
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    if (!task || task.status !== "completed") return;
    const hasFile = Boolean(task.docx_path?.trim() || task.pdf_path?.trim());
    if (!hasFile) return;
    navigate(`/tasks/${task.id}/report`, { replace: true });
  }, [task, navigate]);

  // Derived progress info
  const progressDesc = task ? getProgressDescription(task.status, metrics) : "";

  return (
    <RequireAuth>
      <section className="panel">
        {loading ? <p>加载任务中…</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {task ? (
          <>
            {/* ── Header ── */}
            <div className="section-header">
              <div>
                <h1>{task.company_name}</h1>
                <p className="muted">
                  {getTaskTypeLabel(task.task_type)} · v{task.analyst_version}
                  {task.owner ? ` · ${task.owner}` : ""}
                </p>
              </div>
              <div className="button-row">
                <button
                  className="secondary-button"
                  onClick={() => {
                    if (window.history.length > 1) {
                      navigate(-1);
                      return;
                    }
                    navigate(locationState?.returnTo ?? "/tasks");
                  }}
                  type="button"
                >
                  返回
                </button>
                <Link
                  className="secondary-button link-button"
                  state={{ returnTo: location.pathname, returnLabel: `返回 ${task.company_name}` }}
                  to="/tasks"
                >
                  全部任务
                </Link>
                <Link className="primary-button link-button" to="/dashboard">
                  新建报告
                </Link>
                <span className={`status-pill status-${task.status}`}>
                  {getStatusLabel(task.status)}
                </span>
              </div>
            </div>

            {/* ── Pipeline stepper + progress ── */}
            <PipelineStepper status={task.status} />

            {isRunning ? (
              <div className="progress-banner">
                <span className="progress-pulse" />
                <span className="progress-desc">{progressDesc}</span>
                <ElapsedBadge startedAt={task.created_at} />
              </div>
            ) : null}

            {/* ── Task info ── */}
            <section className="subsection">
              <h2>任务信息</h2>
              <div className="task-meta-grid">
                <div><strong>类型：</strong> {getTaskTypeLabel(task.task_type)}</div>
                <div><strong>分析师数量：</strong> {task.max_analysts}</div>
                <div><strong>关注点：</strong> {task.focus || "默认"}</div>
                {task.target_role ? (
                  <div><strong>目标角色：</strong> {task.target_role}</div>
                ) : null}
                {task.report_review_status ? (
                  <div>
                    <strong>审核：</strong>{" "}
                    <span className={`status-pill status-${task.report_review_status === "pass" ? "completed" : "failed"}`}>
                      {task.report_review_status}
                    </span>
                  </div>
                ) : null}
              </div>
              {task.report_review_summary ? (
                <p className="muted" style={{ marginTop: "0.5rem" }}>{task.report_review_summary}</p>
              ) : null}
            </section>

            {/* ── Analysts ── */}
            <section className="subsection">
              <h2>分析师阵容</h2>
              {task.status === "running_generation" || (task.status === "pending" && !task.analysts_preview.length) ? (
                <AnalystSkeleton count={task.max_analysts} />
              ) : !task.analysts_preview.length ? (
                <p className="muted">暂无分析师数据。</p>
              ) : (
                <div className="task-grid">
                  {task.analysts_preview.map((analyst) => (
                    <article className="panel nested-panel" key={`${analyst.name}-${analyst.role}`}>
                      <h3>{analyst.name || "未命名分析师"}</h3>
                      <p><strong>角色：</strong> {analyst.role || "—"}</p>
                      <p><strong>所属：</strong> {analyst.affiliation || "—"}</p>
                      <p>{analyst.description || "无描述。"}</p>
                    </article>
                  ))}
                </div>
              )}
            </section>

            {/* ── Feedback ── */}
            {task.status === "awaiting_feedback" ? (
              <section className="subsection">
                <h2>人工反馈 · 分析师</h2>
                <p className="muted">
                  对分析师阵容或研究方向添加意见。非空反馈将重新生成分析师。
                  提交空反馈则继续报告生成，不做更改。
                </p>
                <form className="form-stack" onSubmit={handleFeedbackSubmit}>
                  <label>
                    反馈内容
                    <textarea
                      className="feedback-input"
                      onChange={(event) => setFeedback(event.target.value)}
                      placeholder="例如：增加财务尽调角度，或扩展供应链风险方面的访谈要点…"
                      value={feedback}
                    />
                  </label>
                  <div className="button-row">
                    <button className="primary-button" disabled={submitting} type="submit">
                      {submitting ? "提交中…" : "提交反馈"}
                    </button>
                  </div>
                </form>
              </section>
            ) : null}

            {task.status === "running_feedback" ? (
              <section className="subsection">
                <h2>正在执行调研</h2>
                {task.last_feedback?.trim() ? (
                  <label>
                    已提交的反馈
                    <textarea className="feedback-input" readOnly value={task.last_feedback} />
                  </label>
                ) : null}
                {metrics ? (
                  <div className="task-meta-grid" style={{ marginTop: "0.75rem" }}>
                    <div><strong>总耗时：</strong> {(metrics.total_latency_ms / 1000).toFixed(1)}s</div>
                    <div><strong>LLM 调用：</strong> {metrics.call_count}</div>
                    <div><strong>总 Token：</strong> {metrics.total_tokens.toLocaleString()}</div>
                  </div>
                ) : null}
              </section>
            ) : null}

            {/* ── Retry ── */}
            {task.status === "failed" ? (
              <section className="subsection">
                <h2>任务失败</h2>
                {task.error ? <p className="error-text">{task.error}</p> : null}
                {task.failed_stage ? <p className="muted">失败阶段：{task.failed_stage}</p> : null}
                <div className="button-row">
                  <button className="secondary-button" disabled={submitting} onClick={handleRetry} type="button">
                    重试任务
                  </button>
                </div>
              </section>
            ) : null}

            {/* ── Metrics (completed only) ── */}
            {task.status === "completed" && metrics ? (
              <section className="subsection">
                <h2>执行指标</h2>
                <div className="task-meta-grid">
                  <div><strong>总耗时：</strong> {(metrics.total_latency_ms / 1000).toFixed(1)}s</div>
                  <div><strong>LLM 调用：</strong> {metrics.call_count}</div>
                  <div><strong>输入 Token：</strong> {metrics.total_prompt_tokens.toLocaleString()}</div>
                  <div><strong>输出 Token：</strong> {metrics.total_completion_tokens.toLocaleString()}</div>
                  <div><strong>总 Token：</strong> {metrics.total_tokens.toLocaleString()}</div>
                  <div>
                    <strong>预估成本：</strong> ${metrics.estimated_cost_usd.toFixed(4)}
                    {metrics.over_budget ? <span className="error-text"> ⚠️ 超出预算</span> : null}
                  </div>
                </div>
                {Object.keys(metrics.by_node).length > 0 ? (
                  <details style={{ marginTop: "0.75rem" }}>
                    <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                      节点明细（{Object.keys(metrics.by_node).length} 个节点）
                    </summary>
                    <div style={{ marginTop: "0.5rem", overflowX: "auto" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                        <thead>
                          <tr style={{ borderBottom: "2px solid #e5e7eb", textAlign: "left" }}>
                            <th style={{ padding: "6px 8px" }}>节点</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>调用次数</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>耗时</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>Token</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>成本</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(metrics.by_node)
                            .filter(([n]) => !n.startsWith("_total"))
                            .sort(([, a], [, b]) => b.total_duration_ms - a.total_duration_ms)
                            .map(([node, stats]) => (
                              <tr key={node} style={{ borderBottom: "1px solid #f3f4f6" }}>
                                <td style={{ padding: "6px 8px" }}>
                                  <strong>{node}</strong>
                                  {stats.errors > 0 ? <span style={{ color: "#dc2626", marginLeft: "6px" }}>⚠️</span> : null}
                                </td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>{stats.calls}</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>{(stats.total_duration_ms / 1000).toFixed(2)}s</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>{stats.total_tokens.toLocaleString()}</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>${stats.estimated_cost.toFixed(4)}</td>
                              </tr>
                            ))}
                        </tbody>
                        {(() => {
                          const totals = Object.entries(metrics.by_node).filter(([n]) => n.startsWith("_total"));
                          if (!totals.length) return null;
                          return (
                            <tfoot>
                              <tr style={{ borderTop: "2px solid #e5e7eb", fontWeight: 600 }}>
                                <td style={{ padding: "6px 8px" }}>合计</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>{metrics.call_count}</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>{(metrics.total_latency_ms / 1000).toFixed(2)}s</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>{metrics.total_tokens.toLocaleString()}</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>${metrics.estimated_cost_usd.toFixed(4)}</td>
                              </tr>
                            </tfoot>
                          );
                        })()}
                      </table>
                    </div>
                  </details>
                ) : null}
              </section>
            ) : null}
          </>
        ) : null}
      </section>
    </RequireAuth>
  );
}
