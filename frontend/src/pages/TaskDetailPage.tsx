import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task, TaskEvent, TaskMetrics } from "../types";

const ACTIVE_STATUSES = new Set(["pending", "running_generation", "running_feedback"]);

function getStatusLabel(status: string) {
  const statusLabels: Record<string, string> = {
    pending: "Pending",
    running_generation: "Generating",
    awaiting_feedback: "Awaiting feedback",
    running_feedback: "Applying feedback",
    failed: "Failed",
    completed: "Completed",
  };
  return statusLabels[status] ?? status;
}

function getEventLabel(event: string) {
  const eventLabels: Record<string, string> = {
    "task.created": "Task created",
    "task.started": "Task started",
    "task.completed": "Task status updated",
    "task.failed": "Task failed",
    "task.interrupted": "Task interrupted",
    "feedback.submitted": "Feedback submitted",
    "analyst.regenerated": "Analysts regenerated",
    "workflow.configured": "Workflow configured",
    "workflow.skills.assembled": "Skills assembled",
    "workflow.report.status": "Report status updated",
    "company_type.classified": "Company type classified",
    "skills.assembled": "Skills assembled",
    "planner.completed": "Research plan ready",
    "planner.skipped": "Planner skipped",
    "review.report.completed": "Report review completed",
    "review.report.skipped": "Report review skipped",
    "task.evaluation.completed": "Evaluation completed",
  };
  return eventLabels[event] ?? event;
}

function getTaskTypeLabel(taskType: string) {
  const labels: Record<string, string> = {
    due_diligence: "Due Diligence",
    stock_analysis: "Stock Analysis",
    legal_review: "Legal Review",
  };
  return labels[taskType] ?? taskType;
}

export function TaskDetailPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { taskId = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
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

  useEffect(() => {
    if (!taskId) {
      setLoading(false);
      setError("Missing task id.");
      return undefined;
    }

    let cancelled = false;

    const load = async () => {
      try {
        const [nextTask, nextEvents, nextMetrics] = await Promise.all([
          api.getTask(taskId),
          api.getTaskEvents(taskId),
          api.getTaskMetrics(taskId),
        ]);
        if (!cancelled) {
          setTask(nextTask);
          setEvents(nextEvents.events);
          if (nextMetrics) setMetrics(nextMetrics);
          setError("");
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "Failed to load task");
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
      setError(nextError instanceof Error ? nextError.message : "Failed to submit feedback");
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
      setError(nextError instanceof Error ? nextError.message : "Failed to retry task");
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    if (!task || task.status !== "completed") {
      return;
    }
    const hasFile = Boolean(task.docx_path?.trim() || task.pdf_path?.trim());
    if (!hasFile) {
      return;
    }
    navigate(`/tasks/${task.id}/report`, { replace: true });
  }, [task, navigate]);

  return (
    <RequireAuth>
      <section className="panel">
        {loading ? <p>Loading task…</p> : null}
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
                  onClick={() => {
                    if (window.history.length > 1) {
                      navigate(-1);
                      return;
                    }
                    navigate(locationState?.returnTo ?? "/tasks");
                  }}
                  type="button"
                >
                  Back
                </button>
                <Link
                  className="secondary-button link-button"
                  state={{
                    returnTo: location.pathname,
                    returnLabel: `Back to ${task.company_name}`,
                  }}
                  to="/tasks"
                >
                  All tasks
                </Link>
                <Link className="primary-button link-button" to="/dashboard">
                  New report
                </Link>
                <span className={`status-pill status-${task.status}`}>{getStatusLabel(task.status)}</span>
              </div>
            </div>

            {/* Task metadata */}
            <section className="subsection">
              <h2>Task info</h2>
              <div className="task-meta-grid">
                <div>
                  <strong>Type:</strong> {getTaskTypeLabel(task.task_type)}
                </div>
                <div>
                  <strong>Industry:</strong> {task.industry_pack || "—"}
                </div>
                <div>
                  <strong>Max analysts:</strong> {task.max_analysts}
                </div>
                <div>
                  <strong>Focus:</strong> {task.focus || "Default"}
                </div>
                {task.target_role ? (
                  <div>
                    <strong>Target role:</strong> {task.target_role}
                  </div>
                ) : null}
                {task.report_review_status ? (
                  <div>
                    <strong>Review:</strong>{" "}
                    <span className={`status-pill status-${task.report_review_status === "pass" ? "completed" : "failed"}`}>
                      {task.report_review_status}
                    </span>
                  </div>
                ) : null}
              </div>
              {task.report_review_summary ? (
                <p className="muted" style={{ marginTop: "0.5rem" }}>
                  {task.report_review_summary}
                </p>
              ) : null}
            </section>

            {/* Analyst preview */}
            <section className="subsection">
              <h2>Analyst preview</h2>
              {!task.analysts_preview.length ? <p className="muted">No analyst preview yet.</p> : null}
              <div className="task-grid">
                {task.analysts_preview.map((analyst) => (
                  <article className="panel nested-panel" key={`${analyst.name}-${analyst.role}`}>
                    <h3>{analyst.name || "Unnamed analyst"}</h3>
                    <p>
                      <strong>Role:</strong> {analyst.role || "—"}
                    </p>
                    <p>
                      <strong>Affiliation:</strong> {analyst.affiliation || "—"}
                    </p>
                    <p>{analyst.description || "No description."}</p>
                  </article>
                ))}
              </div>
            </section>

            {/* Feedback section */}
            {task.status === "awaiting_feedback" ? (
              <section className="subsection">
                <h2>Human feedback · analysts</h2>
                <p className="muted">
                  Add notes on the analyst lineup or research focus. Non-empty feedback regenerates analysts.
                  Submit empty feedback to continue report generation without changes.
                </p>
                <form className="form-stack" onSubmit={handleFeedbackSubmit}>
                  <label>
                    Feedback
                    <textarea
                      className="feedback-input"
                      onChange={(event) => setFeedback(event.target.value)}
                      placeholder="e.g. Add a finance DD angle, or expand interview points on supply chain risk…"
                      value={feedback}
                    />
                  </label>
                  <div className="button-row">
                    <button className="primary-button" disabled={submitting} type="submit">
                      {submitting ? "Submitting…" : "Submit feedback"}
                    </button>
                  </div>
                </form>
              </section>
            ) : null}

            {task.status === "running_feedback" ? (
              <section className="subsection">
                <h2>Processing your feedback</h2>
                <p className="muted">The pipeline is updating from your feedback—refresh or wait for the status to change.</p>
                <label>
                  Submitted feedback
                  <textarea
                    className="feedback-input"
                    readOnly
                    value={task.last_feedback}
                  />
                </label>
              </section>
            ) : null}

            {/* Retry section */}
            {task.status === "failed" ? (
              <section className="subsection">
                <h2>Task failed</h2>
                {task.error ? <p className="error-text">{task.error}</p> : null}
                {task.failed_stage ? (
                  <p className="muted">Failed at stage: {task.failed_stage}</p>
                ) : null}
                <div className="button-row">
                  <button
                    className="secondary-button"
                    disabled={submitting}
                    onClick={handleRetry}
                    type="button"
                  >
                    Retry task
                  </button>
                </div>
              </section>
            ) : null}

            {/* Metrics section */}
            {metrics ? (
              <section className="subsection">
                <h2>Execution metrics</h2>
                <div className="task-meta-grid">
                  <div><strong>Total duration:</strong> {(metrics.total_latency_ms / 1000).toFixed(1)}s</div>
                  <div><strong>LLM calls:</strong> {metrics.call_count}</div>
                  <div><strong>Prompt tokens:</strong> {metrics.total_prompt_tokens.toLocaleString()}</div>
                  <div><strong>Completion tokens:</strong> {metrics.total_completion_tokens.toLocaleString()}</div>
                  <div><strong>Total tokens:</strong> {metrics.total_tokens.toLocaleString()}</div>
                  <div>
                    <strong>Est. cost:</strong> ${metrics.estimated_cost_usd.toFixed(4)}
                    {metrics.over_budget ? <span className="error-text"> ⚠️ Over budget</span> : null}
                  </div>
                </div>
                {Object.keys(metrics.by_node).length > 0 ? (
                  <details style={{ marginTop: "0.75rem" }}>
                    <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                      Per-node breakdown ({Object.keys(metrics.by_node).length} nodes)
                    </summary>
                    <div style={{ marginTop: "0.5rem", overflowX: "auto" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
                        <thead>
                          <tr style={{ borderBottom: "2px solid #e5e7eb", textAlign: "left" }}>
                            <th style={{ padding: "6px 8px" }}>Node</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>Calls</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>Duration</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>Tokens</th>
                            <th style={{ padding: "6px 8px", textAlign: "right" }}>Cost</th>
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
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>
                                  {(stats.total_duration_ms / 1000).toFixed(2)}s
                                </td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>
                                  {stats.total_tokens.toLocaleString()}
                                </td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>
                                  ${stats.estimated_cost.toFixed(4)}
                                </td>
                              </tr>
                            ))}
                        </tbody>
                        {/* Totals row */}
                        {(() => {
                          const totals = Object.entries(metrics.by_node).filter(([n]) => n.startsWith("_total"));
                          if (!totals.length) return null;
                          return (
                            <tfoot>
                              <tr style={{ borderTop: "2px solid #e5e7eb", fontWeight: 600 }}>
                                <td style={{ padding: "6px 8px" }}>Total</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>{metrics.call_count}</td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>
                                  {(metrics.total_latency_ms / 1000).toFixed(2)}s
                                </td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>
                                  {metrics.total_tokens.toLocaleString()}
                                </td>
                                <td style={{ padding: "6px 8px", textAlign: "right" }}>
                                  ${metrics.estimated_cost_usd.toFixed(4)}
                                </td>
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

            {/* Events */}
            <section className="subsection">
              <h2>Events</h2>
              {!events.length ? <p className="muted">No events yet.</p> : null}
              {events.length > 0 ? (
                <div className="event-log-scroll">
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
                </div>
              ) : null}
            </section>
          </>
        ) : null}
      </section>
    </RequireAuth>
  );
}
