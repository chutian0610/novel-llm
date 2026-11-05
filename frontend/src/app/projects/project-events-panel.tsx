"use client";

import { useProjectEvents } from "./use-project-events";

const EVENT_LABELS: Record<string, string> = {
  "project.created": "创建项目",
  "project.updated": "更新项目",
  "project.archived": "归档项目",
};

const CONNECTION_LABELS = {
  connecting: "连接中……",
  live: "已连接",
  reconnecting: "断线,重连中……",
  unavailable: "连接不可用",
} as const;

function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? eventType;
}

export function ProjectEventsPanel({ projectId }: { projectId: string }) {
  const { events, connection } = useProjectEvents(projectId);

  return (
    <div data-testid="event-panel">
      <h3>事件流</h3>
      <p data-testid="event-connection">{CONNECTION_LABELS[connection]}</p>
      {events.length === 0 ? (
        <p data-testid="event-empty">暂无事件</p>
      ) : (
        <ul data-testid="event-list">
          {events.map((envelope) => (
            <li key={envelope.eventId} data-testid="event-item">
              {eventLabel(envelope.eventType)}
              {typeof envelope.data.title === "string" && (
                <span> —— {envelope.data.title}</span>
              )}
              <time>{envelope.occurredAt}</time>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
