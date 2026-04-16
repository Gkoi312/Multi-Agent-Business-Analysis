import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";
import type { Task, TaskEvent } from "../types";

const ACTIVE_STATUSES = new Set(["pending", "blocked", "running_generation", "running_feedback"]);

function fileNameFromPath(path: string) {
  return path.split(/[/\\]/).pop() ?? path;
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
          setError(nextError instanceof Error ? nextError.message : "Unable to load task");
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
      setError(nextError instanceof Error ? nextError.message : "Unable to submit feedback");
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
      setError(nextError instanceof Error ? nextError.message : "Unable to retry task");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <RequireAuth>
      <section className="panel">
        {loading ? <p>Loading task...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {task ? (
          <>
            <div className="section-header">
              <div>
                <h1>{task.company_name}</h1>
                <p className="muted">Task ID: {task.id}</p>
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
                  View all tasks
                </Link>
                <Link className="primary-button link-button" to="/dashboard">
                  Create new report
                </Link>
                <span className={`status-pill status-${task.status}`}>{task.status}</span>
              </div>
            </div>

            <div className="detail-grid">
              <div className="detail-item">
                <strong>Focus</strong>
                <span>{task.focus || "Default due diligence focus"}</span>
              </div>
              <div className="detail-item">
                <strong>Target role</strong>
                <span>{task.target_role || "Not specified"}</span>
              </div>
              <div className="detail-item">
                <strong>Recommendation</strong>
                <span>{task.final_recommendation || "Pending"}</span>
              </div>
              <div className="detail-item">
                <strong>Risk summary</strong>
                <span>
                  High {task.risk_summary.high} / Medium {task.risk_summary.medium} / Low {task.risk_summary.low}
                </span>
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
                      <strong>Role:</strong> {analyst.role || "N/A"}
                    </p>
                    <p>
                      <strong>Affiliation:</strong> {analyst.affiliation || "N/A"}
                    </p>
                    <p>{analyst.description || "No description yet."}</p>
                  </article>
                ))}
              </div>
            </section>

            <section className="subsection">
              <h2>Feedback</h2>
              <form className="form-stack" onSubmit={handleFeedbackSubmit}>
                <textarea
                  onChange={(event) => setFeedback(event.target.value)}
                  placeholder="Leave feedback to regenerate analysts, or submit empty feedback to continue the workflow."
                  rows={5}
                  value={feedback}
                />
                <div className="button-row">
                  <button
                    className="primary-button"
                    disabled={submitting || task.status === "running_feedback"}
                    type="submit"
                  >
                    {submitting ? "Submitting..." : "Submit feedback"}
                  </button>
                  {task.status === "failed" ? (
                    <button
                      className="secondary-button"
                      disabled={submitting}
                      onClick={handleRetry}
                      type="button"
                    >
                      Retry task
                    </button>
                  ) : null}
                </div>
              </form>
            </section>

            <section className="subsection">
              <h2>Downloads</h2>
              {!downloadLinks.length ? <p className="muted">Report files are not available yet.</p> : null}
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
              <h2>Events</h2>
              {!events.length ? <p className="muted">No task events yet.</p> : null}
              <div className="event-list">
                {events.map((event) => (
                  <article className="event-item" key={`${event.task_id}-${event.ts}-${event.event}`}>
                    <div className="event-row">
                      <strong>{event.event}</strong>
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
