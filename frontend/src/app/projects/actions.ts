"use server";

/**
 * Project actions: the browser never reaches the API directly (only the web
 * interface is published, ADR-0005), so the workbench talks to /api/v1 through
 * these server actions. They reuse the generated typed client, so the workbench
 * cannot drift from the API contract (ADR-0008).
 */

import { apiClient } from "../../lib/api/client";

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

const ERROR_LABELS: Record<string, string> = {
  idempotency_key_conflict: "幂等键已被不同的请求使用,请重试",
  project_archived: "项目已归档",
  project_not_found: "项目不存在",
  etag_mismatch: "版本已变化,请刷新后重试",
  missing_if_match: "缺少 If-Match 头",
  malformed_if_match: "If-Match 头格式不正确",
  invalid_cursor: "无效的分页游标",
  validation_error: "请求校验失败",
};

function problemMessage(error: unknown): string {
  const body = (error ?? {}) as { code?: string; detail?: string; title?: string };
  const label = body.code ? (ERROR_LABELS[body.code] ?? body.title ?? body.code) : "请求失败";
  return body.detail ? `${label}(${body.detail})` : label;
}

export async function listProjects(): Promise<ActionResult<ProjectView[]>> {
  const { data, error } = await apiClient.GET("/api/v1/projects", {
    params: { query: { limit: 100 } },
  });
  if (error !== undefined) {
    return { ok: false, error: problemMessage(error) };
  }
  return {
    ok: true,
    value: data.items.map((item) => ({
      id: item.id,
      title: item.title,
      description: item.description,
      language: item.language,
      status: item.status,
      createdAt: item.created_at,
      updatedAt: item.updated_at,
    })),
  };
}

export async function createProject(formData: FormData): Promise<ActionResult<ProjectView>> {
  const title = String(formData.get("title") ?? "").trim();
  const description = String(formData.get("description") ?? "").trim();
  const { data, error, response } = await apiClient.POST("/api/v1/projects", {
    headers: {
      // A fresh key per create attempt: a retried submission replays instead
      // of duplicating (ADR-0008).
      "Idempotency-Key": crypto.randomUUID(),
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
  void response;
  return {
    ok: true,
    value: {
      id: data.id,
      title: data.title,
      description: data.description,
      language: data.language,
      status: data.status,
      createdAt: data.created_at,
      updatedAt: data.updated_at,
    },
  };
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
  return {
    ok: true,
    value: {
      id: data.id,
      title: data.title,
      description: data.description,
      language: data.language,
      status: data.status,
      createdAt: data.created_at,
      updatedAt: data.updated_at,
    },
  };
}
