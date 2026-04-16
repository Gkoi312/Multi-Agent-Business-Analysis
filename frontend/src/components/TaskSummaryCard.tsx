import { Link } from "react-router-dom";

import type { Task } from "../types";

type TaskSummaryCardProps = {
  task: Task;
  state?: Record<string, unknown>;
};

export function TaskSummaryCard({ task, state }: TaskSummaryCardProps) {
  return (
    <article className="panel task-card">
      <div className="task-card-header">
        <div>
          <h3>{task.company_name}</h3>
          <p className="muted">Task ID: {task.id}</p>
        </div>
        <span className={`status-pill status-${task.status}`}>{task.status}</span>
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
      <Link className="secondary-button link-button" state={state} to={`/tasks/${task.id}`}>
        View details
      </Link>
    </article>
  );
}
