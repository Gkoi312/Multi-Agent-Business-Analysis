import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task, TaskEvent } from "../types";

const ACTIVE_STATUSES = new Set(["pending", "blocked", "running_generation", "running_feedback"]);

function fileNameFromPath(path: string) {
  return path.split(/[/\\]/).pop() ?? path;
}

function getStatusLabel(status: string) {
  const statusLabels: Record<string, string> = {
    pending: "待开始",
    blocked: "已阻塞",
    running_generation: "生成中",
    awaiting_feedback: "待反馈",
    running_feedback: "处理反馈中",
    failed: "失败",
    completed: "已完成",
  };
  return statusLabels[status] ?? status;
}

function getEventLabel(event: string) {
  const eventLabels: Record<string, string> = {
    "task.created": "任务已创建",
    "task.started": "任务已开始",
    "task.completed": "任务状态已更新",
    "task.failed": "任务失败",
    "task.retrying": "任务自动重试中",
    "task.interrupted": "任务被中断",
    "task.claimed": "任务已领取",
    "task.dependencies.updated": "任务依赖已更新",
    "feedback.submitted": "反馈已提交",
    "analyst.regenerated": "分析师已重新生成",
  };
  return eventLabels[event] ?? event;
}

export function TaskDetailPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { taskId = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const locationState = location.state as
    | { fromTasks?: boolean; returnTo?: string; returnLabel?: string }
    | null;

  useEffect(() => {
    if (!taskId) {
      return undefined;
    }

    let cancelled = false;

    const load = async () => {
      try {
        const [nextTask, nextEvents] = await Promise.all([
          api.getTask(taskId),
          api.getTaskEvents(taskId),
        ]);
        if (!cancelled) {
          setTask(nextTask);
          setEvents(nextEvents.events);
          setError("");
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "无法加载任务详情");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();
    const interval = window.setInterval(() => {
      if (task && !ACTIVE_STATUSES.has(task.status)) {
        return;
      }
      void load();
    }, 2500);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [taskId, task]);

  const downloadLinks = useMemo(() => {
    if (!task) {
      return [];
    }
    return [task.docx_path, task.pdf_path]
      .filter(Boolean)
      .map((path) => ({
        href: api.buildDownloadUrl(task.id, fileNameFromPath(path)),
        label: fileNameFromPath(path),
      }));
  }, [task]);

  const handleFeedbackSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!task) {
      return;
    }
    setSubmitting(true);
    try {
      const { task: updatedTask } = await api.submitFeedback(task.id, { feedback });
      setTask(updatedTask);
      setFeedback("");
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "无法提交反馈");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = async () => {
    if (!task) {
      return;
    }
    setSubmitting(true);
    try {
      await api.retryTask(task.id);
      const refreshed = await api.getTask(task.id);
      setTask(refreshed);
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "无法重试任务");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <RequireAuth>
      <section className="panel">
        {loading ? <p>正在加载任务详情...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {task ? (
          <>
            <div className="section-header">
              <div>
                <h1>{task.company_name}</h1>
                <p className="muted">任务 ID：{task.id}</p>
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
                  state={{
                    returnTo: location.pathname,
                    returnLabel: `返回 ${task.company_name}`,
                  }}
                  to="/tasks"
                >
                  查看全部任务
                </Link>
                <Link className="primary-button link-button" to="/dashboard">
                  新建报告
                </Link>
                <span className={`status-pill status-${task.status}`}>{getStatusLabel(task.status)}</span>
              </div>
            </div>

            <div className="detail-grid">
              <div className="detail-item">
                <strong>关注重点</strong>
                <span>{task.focus || "默认尽职调查重点"}</span>
              </div>
              <div className="detail-item">
                <strong>目标岗位</strong>
                <span>{task.target_role || "未指定"}</span>
              </div>
              <div className="detail-item">
                <strong>最终建议</strong>
                <span>{task.final_recommendation || "待生成"}</span>
              </div>
              <div className="detail-item">
                <strong>风险汇总</strong>
                <span>
                  高 {task.risk_summary.high} / 中 {task.risk_summary.medium} / 低 {task.risk_summary.low}
                </span>
              </div>
            </div>

            <section className="subsection">
              <h2>分析师预览</h2>
              {!task.analysts_preview.length ? <p className="muted">暂无分析师预览。</p> : null}
              <div className="task-grid">
                {task.analysts_preview.map((analyst) => (
                  <article className="panel nested-panel" key={`${analyst.name}-${analyst.role}`}>
                    <h3>{analyst.name || "未命名分析师"}</h3>
                    <p>
                      <strong>角色：</strong> {analyst.role || "暂无"}
                    </p>
                    <p>
                      <strong>所属：</strong> {analyst.affiliation || "暂无"}
                    </p>
                    <p>{analyst.description || "暂无描述。"}</p>
                  </article>
                ))}
              </div>
            </section>

            <section className="subsection">
              <h2>反馈</h2>
              <form className="form-stack" onSubmit={handleFeedbackSubmit}>
                <textarea
                  onChange={(event) => setFeedback(event.target.value)}
                  placeholder="输入反馈可重新生成分析师；如果直接提交空反馈，则继续后续流程。"
                  rows={5}
                  value={feedback}
                />
                <div className="button-row">
                  <button
                    className="primary-button"
                    disabled={submitting || task.status === "running_feedback"}
                    type="submit"
                  >
                    {submitting ? "提交中..." : "提交反馈"}
                  </button>
                  {task.status === "failed" ? (
                    <button
                      className="secondary-button"
                      disabled={submitting}
                      onClick={handleRetry}
                      type="button"
                    >
                      重试任务
                    </button>
                  ) : null}
                </div>
              </form>
            </section>

            <section className="subsection">
              <h2>下载</h2>
              {!downloadLinks.length ? <p className="muted">报告文件暂未生成。</p> : null}
              <div className="button-row">
                {downloadLinks.map((item) => (
                  <a
                    className="secondary-button link-button"
                    href={item.href}
                    key={item.href}
                    rel="noreferrer"
                    target="_blank"
                  >
                    {item.label}
                  </a>
                ))}
              </div>
            </section>

            <section className="subsection">
              <h2>事件记录</h2>
              {!events.length ? <p className="muted">暂无任务事件。</p> : null}
              <div className="event-list">
                {events.map((event) => (
                  <article className="event-item" key={`${event.task_id}-${event.ts}-${event.event}`}>
                    <div className="event-row">
                      <strong>{getEventLabel(event.event)}</strong>
                      <span className="muted">
                        {new Date(event.ts * 1000).toLocaleString()}
                      </span>
                    </div>
                    <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                  </article>
                ))}
              </div>
            </section>
          </>
        ) : null}
      </section>
    </RequireAuth>
  );
}
