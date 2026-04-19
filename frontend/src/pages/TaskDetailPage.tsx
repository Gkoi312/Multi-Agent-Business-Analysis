import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task, TaskEvent } from "../types";

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

            {task.status === "failed" ? (
              <section className="subsection">
                <h2>Task failed</h2>
                {task.error ? <p className="error-text">{task.error}</p> : null}
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
