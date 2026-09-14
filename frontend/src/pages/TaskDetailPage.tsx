import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task, TaskMetrics } from "../types";

const ACTIVE_STATUSES = new Set(["pending", "running_generation", "running_feedback"]);

function getStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    running_generation: "生成中",
    awaiting_feedback: "等待反馈",
    running_feedback: "研究中",
    failed: "失败",
    completed: "已完成",
  };
  return labels[status] ?? status;
}

function getTaskTypeLabel(taskType: string) {
  const labels: Record<string, string> = {
    due_diligence: "AI 科技公司调研",
  };
  return labels[taskType] ?? taskType;
}

function getProgressDescription(status: string, metrics: TaskMetrics | null): string {
  if (status === "pending") return "正在创建任务...";
  if (status === "running_generation") return "正在加载技能包并生成分析师团队...";
  if (status === "awaiting_feedback") return "请审核分析师阵容，提交空反馈即可继续生成报告。";
  if (status === "running_feedback") {
    const calls = metrics?.call_count ?? 0;
    return calls > 0 ? `正在执行联网研究，已调用 ${calls} 次 LLM...` : "正在执行联网研究...";
  }
  if (status === "completed") return "报告已生成。";
  if (status === "failed") return "任务失败，可尝试从 checkpoint 重试。";
  return "";
}

function activeStepIndex(status: string): number {
  if (status === "pending" || status === "running_generation") return 0;
  if (status === "awaiting_feedback") return 1;
  if (status === "running_feedback") return 2;
  if (status === "completed") return 3;
  return -1;
}

function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (h > 0) return `${h}:${pad(m)}:${pad(sec)}`;
  return `${m}:${pad(sec)}`;
}

const PIPELINE_STEPS = ["生成分析师", "等待反馈", "执行研究", "完成报告"];

function PipelineStepper({ status }: { status: string }) {
  const active = activeStepIndex(status);
  return (
    <div className="pipeline-stepper">
      {PIPELINE_STEPS.map((label, index) => {
        let cls = "pipeline-step";
        if (index < active) cls += " is-done";
        else if (index === active) cls += " is-active";
        return (
          <div className={cls} key={label}>
            <span className="pipeline-step-icon">{index + 1}</span>
            <span className="pipeline-step-label">{label}</span>
          </div>
        );
      })}
    </div>
  );
}

function ElapsedBadge({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const tick = () => setElapsed(Date.now() / 1000 - startedAt);
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [startedAt]);

  return <span className="elapsed-badge">{formatElapsed(elapsed)}</span>;
}

function AnalystSkeleton({ count }: { count: number }) {
  return (
    <div className="task-grid">
      {Array.from({ length: count }).map((_, index) => (
        <article className="panel nested-panel skeleton-card" key={index}>
          <div className="skeleton-line skeleton-title" />
          <div className="skeleton-line skeleton-text" />
          <div className="skeleton-line skeleton-text short" />
          <div className="skeleton-line skeleton-text" />
        </article>
      ))}
    </div>
  );
}

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
  const locationState = location.state as { returnTo?: string; returnLabel?: string } | null;
  const taskRef = useRef<Task | null>(null);
  taskRef.current = task;

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
          setMetrics(nextMetrics);
          setError("");
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "加载任务失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    setLoading(true);
    void load();
    const interval = window.setInterval(() => {
      const current = taskRef.current;
      if (current && !ACTIVE_STATUSES.has(current.status)) return;
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
    if (hasFile) navigate(`/tasks/${task.id}/report`, { replace: true });
  }, [task, navigate]);

  return (
    <RequireAuth>
      <section className="panel">
        {loading ? <p>加载任务中...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {task ? (
          <>
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
                  onClick={() => navigate(locationState?.returnTo ?? "/tasks")}
                  type="button"
                >
                  返回
                </button>
                <Link className="secondary-button link-button" to="/tasks">
                  全部任务
                </Link>
                <Link className="primary-button link-button" to="/dashboard">
                  新建报告
                </Link>
                <span className={`status-pill status-${task.status}`}>{getStatusLabel(task.status)}</span>
              </div>
            </div>

            <PipelineStepper status={task.status} />

            {ACTIVE_STATUSES.has(task.status) ? (
              <div className="progress-banner">
                <span className="progress-pulse" />
                <span className="progress-desc">{getProgressDescription(task.status, metrics)}</span>
                <ElapsedBadge startedAt={task.created_at} />
              </div>
            ) : null}

            <section className="subsection">
              <h2>任务信息</h2>
              <div className="task-meta-grid">
                <div><strong>类型：</strong> {getTaskTypeLabel(task.task_type)}</div>
                <div><strong>分析师数量：</strong> {task.max_analysts}</div>
                <div><strong>关注点：</strong> {task.focus || "默认"}</div>
                {task.target_role ? <div><strong>目标角色：</strong> {task.target_role}</div> : null}
                {task.report_review_status ? <div><strong>审核：</strong> {task.report_review_status}</div> : null}
              </div>
            </section>

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
                      <p><strong>角色：</strong> {analyst.role || "未指定"}</p>
                      <p><strong>所属：</strong> {analyst.affiliation || "未指定"}</p>
                      <p>{analyst.description || "暂无描述。"}</p>
                    </article>
                  ))}
                </div>
              )}
            </section>

            {task.status === "awaiting_feedback" ? (
              <section className="subsection">
                <h2>人工反馈</h2>
                <p className="muted">
                  对分析师阵容或研究方向添加意见。提交空反馈会继续执行研究和报告生成。
                </p>
                <form className="form-stack" onSubmit={handleFeedbackSubmit}>
                  <label>
                    反馈内容
                    <textarea
                      className="feedback-input"
                      onChange={(event) => setFeedback(event.target.value)}
                      placeholder="例如：增加财务尽调视角，或扩展供应链风险方面的访谈要点。"
                      value={feedback}
                    />
                  </label>
                  <div className="button-row">
                    <button className="primary-button" disabled={submitting} type="submit">
                      {submitting ? "提交中..." : "提交反馈"}
                    </button>
                  </div>
                </form>
              </section>
            ) : null}

            {task.status === "running_feedback" && metrics ? (
              <section className="subsection">
                <h2>执行指标</h2>
                <div className="task-meta-grid">
                  <div><strong>总耗时：</strong> {(metrics.total_latency_ms / 1000).toFixed(1)}s</div>
                  <div><strong>LLM 调用：</strong> {metrics.call_count}</div>
                  <div><strong>总 Token：</strong> {metrics.total_tokens.toLocaleString()}</div>
                </div>
              </section>
            ) : null}

            {task.status === "failed" ? (
              <section className="subsection">
                <h2>任务失败</h2>
                {task.error ? <p className="error-text">{task.error}</p> : null}
                {task.failed_stage ? <p className="muted">失败阶段：{task.failed_stage}</p> : null}
                <button className="secondary-button" disabled={submitting} onClick={handleRetry} type="button">
                  重试任务
                </button>
              </section>
            ) : null}
          </>
        ) : null}
      </section>
    </RequireAuth>
  );
}
