import { beforeEach, describe, expect, it, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("../../lib/api/client", () => ({
  apiClient: { GET: get, POST: post },
}));

import { archiveProject, createProject, listProjects } from "./actions";

const PROJECT = {
  id: "0199e0a2-0000-7000-8000-000000000001",
  title: "长夜灯",
  description: null,
  language: "zh-Hans",
  status: "active",
  created_at: "2026-11-05T08:00:00Z",
  updated_at: "2026-11-05T08:00:00Z",
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe("createProject(创建项目)", () => {
  it("携带 Idempotency-Key 并返回项目视图", async () => {
    post.mockResolvedValueOnce({ data: PROJECT, error: undefined });

    const formData = new FormData();
    formData.set("title", "长夜灯");
    formData.set("description", "一部志怪长篇");

    const result = await createProject(formData);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.title).toBe("长夜灯");
      expect(result.value.status).toBe("active");
    }
    const [path, init] = post.mock.calls[0];
    expect(path).toBe("/api/v1/projects");
    expect(init.headers["Idempotency-Key"]).toEqual(expect.any(String));
    expect(init.body).toEqual({ title: "长夜灯", description: "一部志怪长篇", language: "zh-Hans" });
  });

  it("把稳定错误码映射为中文提示", async () => {
    post.mockResolvedValueOnce({
      data: undefined,
      error: { code: "idempotency_key_conflict", detail: "payload differs" },
    });

    const result = await createProject(new FormData());

    expect(result).toEqual({
      ok: false,
      error: "幂等键已被不同的请求使用,请重试(payload differs)",
    });
  });
});

describe("archiveProject(归档项目)", () => {
  it("先读取当前 ETag,再携带 If-Match 归档", async () => {
    get.mockResolvedValueOnce({
      data: PROJECT,
      error: undefined,
      response: { headers: { get: (name: string) => (name === "ETag" ? '"7"' : null) } },
    });
    post.mockResolvedValueOnce({ data: { ...PROJECT, status: "archived" }, error: undefined });

    const result = await archiveProject(PROJECT.id);

    expect(result.ok).toBe(true);
    expect(get).toHaveBeenCalledWith("/api/v1/projects/{project_id}", {
      params: { path: { project_id: PROJECT.id } },
    });
    const [path, init] = post.mock.calls[0];
    expect(path).toBe("/api/v1/projects/{project_id}/archive");
    expect(init.headers).toEqual({ "If-Match": '"7"' });
  });

  it("读取失败时直接返回错误,不发起归档", async () => {
    get.mockResolvedValueOnce({
      data: undefined,
      error: { code: "project_not_found" },
      response: { headers: { get: () => null } },
    });

    const result = await archiveProject("missing");

    expect(result).toEqual({ ok: false, error: "项目不存在" });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("listProjects(项目列表)", () => {
  it("映射分页负载", async () => {
    get.mockResolvedValueOnce({
      data: { items: [PROJECT], next_cursor: null },
      error: undefined,
    });

    const result = await listProjects();

    expect(result).toEqual({
      ok: true,
      value: [
        {
          id: PROJECT.id,
          title: PROJECT.title,
          description: null,
          language: "zh-Hans",
          status: "active",
          createdAt: PROJECT.created_at,
          updatedAt: PROJECT.updated_at,
        },
      ],
    });
  });
});
