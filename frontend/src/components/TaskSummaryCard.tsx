import { Link } from "react-router-dom";

import type { Task } from "../types";

type TaskSummaryCardProps = {
  task: Task;
  state?: Record<string, unknown>;
};

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

export function TaskSummaryCard({ task, state }: TaskSummaryCardProps) {
  return (
    <article className="panel task-card">
      <div className="task-card-header">
        <div>
          <h3>{task.company_name}</h3>
          <p className="muted">
            {getTaskTypeLabel(task.task_type)}
            {task.max_analysts ? ` · ${task.max_analysts} 位分析师` : ""}
          </p>
        </div>
        <span className={`status-pill status-${task.status}`}>{getStatusLabel(task.status)}</span>
      </div>
      <p>
        <strong>关注点：</strong> {task.focus || "默认关注点"}
      </p>
      <p>
        <strong>目标角色：</strong> {task.target_role || "未指定"}
      </p>
      {task.report_review_status ? (
        <p>
          <strong>审核：</strong> {task.report_review_status}
        </p>
      ) : null}
      <p>
        <strong>更新于：</strong> {new Date(task.updated_at * 1000).toLocaleString()}
      </p>
      <Link
        className="secondary-button link-button"
        state={state}
        to={task.status === "completed" ? `/tasks/${task.id}/report` : `/tasks/${task.id}`}
      >
        {task.status === "completed" ? "查看报告" : "查看详情"}
      </Link>
    </article>
  );
}
