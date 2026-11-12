"use server";

/**
 * Project actions: the browser never reaches the API directly (only the web
 * interface is published, ADR-0005), so the workbench talks to /api/v1 through
 * these server actions. They reuse the generated typed client, so the workbench
 * cannot drift from the API contract (ADR-0008).
 */

import { apiClient } from "../../lib/api/client";
import { problemMessage } from "../../lib/api/problems";
import type { components } from "../../lib/api/schema";

type ProjectResource = components["schemas"]["ProjectResource"];

export interface ProjectView {
  id: string;
  title: string;
  description: string | null;
  language: string;
  status: string;
  createdAt: string;
  updatedAt: string;
}

export type ActionResult<T> = { ok: true; value: T } | { ok: false; error: string };

function toProjectView(data: ProjectResource): ProjectView {
  return {
    id: data.id,
    title: data.title,
    description: data.description,
    language: data.language,
    status: data.status,
    createdAt: data.created_at,
    updatedAt: data.updated_at,
  };
}

// The list endpoint pages at limit ≤ 100; walk the cursor so the workbench
// never silently truncates. 20 pages ≈ 2000 projects is a safe stop for a
// single-author workbench.
const MAX_PAGES = 20;

export async function listProjects(): Promise<ActionResult<ProjectView[]>> {
  const items: ProjectView[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page++) {
    const { data, error } = await apiClient.GET("/api/v1/projects", {
      params: { query: { limit: 100, ...(cursor !== undefined ? { cursor } : {}) } },
    });
    if (error !== undefined) {
      return { ok: false, error: problemMessage(error) };
    }
    items.push(...data.items.map(toProjectView));
    if (data.next_cursor === null) {
      return { ok: true, value: items };
    }
    cursor = data.next_cursor;
  }
  return { ok: true, value: items };
}

export async function createProject(
  formData: FormData,
  idempotencyKey: string,
): Promise<ActionResult<ProjectView>> {
  const title = String(formData.get("title") ?? "").trim();
  const description = String(formData.get("description") ?? "").trim();
  const { data, error } = await apiClient.POST("/api/v1/projects", {
    params: {
      header: {
        // The caller holds the key across retries of one create intent, so a
        // resubmission replays instead of duplicating (ADR-0008).
        "Idempotency-Key": idempotencyKey,
      },
    },
    body: {
      title,
      // 界面默认简体中文(用户故事 20);Project 仍存配置的语言。
      language: "zh-Hans",
      ...(description ? { description } : {}),
    },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toProjectView(data) };
}

export async function getProject(projectId: string): Promise<ActionResult<ProjectView>> {
  const { data, error } = await apiClient.GET("/api/v1/projects/{project_id}", {
    params: { path: { project_id: projectId } },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toProjectView(data) };
}

export async function archiveProject(projectId: string): Promise<ActionResult<ProjectView>> {
  // Optimistic concurrency from the workbench: read the current ETag, then
  // archive with If-Match (ADR-0008).
  const detail = await apiClient.GET("/api/v1/projects/{project_id}", {
    params: { path: { project_id: projectId } },
  });
  if (detail.error !== undefined) {
    return { ok: false, error: problemMessage(detail.error) };
  }
  const etag = detail.response.headers.get("ETag") ?? undefined;

  const { data, error } = await apiClient.POST("/api/v1/projects/{project_id}/archive", {
    params: { path: { project_id: projectId } },
    headers: etag ? { "If-Match": etag } : undefined,
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return { ok: true, value: toProjectView(data) };
}
