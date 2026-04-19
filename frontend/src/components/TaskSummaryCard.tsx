import { Link } from "react-router-dom";

import type { Task } from "../types";

type TaskSummaryCardProps = {
  task: Task;
  state?: Record<string, unknown>;
};

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

export function TaskSummaryCard({ task, state }: TaskSummaryCardProps) {
  return (
    <article className="panel task-card">
      <div className="task-card-header">
        <div>
          <h3>{task.company_name}</h3>
          <p className="muted">Task ID: {task.id}</p>
        </div>
        <span className={`status-pill status-${task.status}`}>{getStatusLabel(task.status)}</span>
      </div>
      <p>
        <strong>Focus:</strong> {task.focus || "Default due diligence focus"}
      </p>
      <p>
        <strong>Target role:</strong> {task.target_role || "Not specified"}
      </p>
      <p>
        <strong>Updated:</strong> {new Date(task.updated_at * 1000).toLocaleString()}
      </p>
      <Link
        className="secondary-button link-button"
        state={state}
        to={task.status === "completed" ? `/tasks/${task.id}/report` : `/tasks/${task.id}`}
      >
        {task.status === "completed" ? "View report" : "View details"}
      </Link>
    </article>
  );
}
