import { beforeEach, describe, expect, it, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("../../lib/api/client", () => ({
  apiClient: { GET: get, POST: post },
}));

import { archiveProject, createProject, getProject, listProjects } from "./actions";

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
  it("使用调用方持有的 Idempotency-Key,重试复用同一键", async () => {
    post.mockResolvedValue({ data: PROJECT, error: undefined });

    const formData = new FormData();
    formData.set("title", "长夜灯");
    formData.set("description", "一部志怪长篇");

    const first = await createProject(formData, "intent-key-1");
    const retry = await createProject(formData, "intent-key-1");

    expect(first.ok).toBe(true);
    expect(retry.ok).toBe(true);
    expect(post).toHaveBeenCalledTimes(2);
    for (const [path, init] of post.mock.calls) {
      expect(path).toBe("/api/v1/projects");
      expect(init.params.header["Idempotency-Key"]).toBe("intent-key-1");
      expect(init.body).toEqual({ title: "长夜灯", description: "一部志怪长篇", language: "zh-Hans" });
    }
  });

  it("把稳定错误码映射为中文提示", async () => {
    post.mockResolvedValue({
      data: undefined,
      error: { code: "idempotency_key_conflict", detail: "payload differs" },
    });

    const result = await createProject(new FormData(), "intent-key-2");

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
  it("跟随 next_cursor 翻页,不静默截断", async () => {
    const pageOne = { ...PROJECT, id: "p1" };
    const pageTwo = { ...PROJECT, id: "p2", title: "第二页之书" };
    get
      .mockResolvedValueOnce({
        data: { items: [pageOne], next_cursor: "cursor-1" },
        error: undefined,
      })
      .mockResolvedValueOnce({
        data: { items: [pageTwo], next_cursor: null },
        error: undefined,
      });

    const result = await listProjects();

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.map((project) => project.id)).toEqual(["p1", "p2"]);
    }
    expect(get).toHaveBeenCalledTimes(2);
    expect(get.mock.calls[0][1].params.query).toEqual({ limit: 100 });
    expect(get.mock.calls[1][1].params.query).toEqual({ limit: 100, cursor: "cursor-1" });
  });
});

describe("getProject(打开项目)", () => {
  it("返回项目视图", async () => {
    get.mockResolvedValueOnce({ data: PROJECT, error: undefined, response: {} });

    const result = await getProject(PROJECT.id);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.title).toBe("长夜灯");
    }
  });
});
