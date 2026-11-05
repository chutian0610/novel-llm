import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listProjects, createProject, getProject, archiveProject } = vi.hoisted(() => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  getProject: vi.fn(),
  archiveProject: vi.fn(),
}));

vi.mock("./actions", () => ({ listProjects, createProject, getProject, archiveProject }));

import { ProjectPanel } from "./project-panel";
import type { ProjectView } from "./actions";

function project(overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    id: "0199e0a2-0000-7000-8000-000000000001",
    title: "长夜灯",
    description: "一部志怪长篇",
    language: "zh-Hans",
    status: "active",
    createdAt: "2026-11-05T08:00:00Z",
    updatedAt: "2026-11-05T08:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  listProjects.mockReset().mockResolvedValue({ ok: true, value: [] });
  createProject.mockReset();
  getProject.mockReset();
  archiveProject.mockReset();
});

describe("ProjectPanel(项目面板)", () => {
  it("载入后显示项目列表、状态与归档按钮", async () => {
    listProjects.mockResolvedValue({
      ok: true,
      value: [
        project(),
        project({ id: "p2", title: "封存之书", status: "archived", description: null }),
      ],
    });

    render(<ProjectPanel />);

    expect(await screen.findByText("长夜灯")).toBeInTheDocument();
    expect(screen.getByText(/一部志怪长篇/)).toBeInTheDocument();
    expect(screen.getByTestId("status-active")).toHaveTextContent("进行中");
    expect(screen.getByTestId("status-archived")).toHaveTextContent("已归档");
    // 归档按钮只出现在进行中的项目上
    expect(screen.getAllByRole("button", { name: "归档" })).toHaveLength(1);
  });

  it("空列表显示引导文案", async () => {
    render(<ProjectPanel />);
    expect(await screen.findByTestId("empty-projects")).toHaveTextContent("暂无项目");
  });

  it("提交创建表单携带幂等键,成功后为新意图换键", async () => {
    createProject.mockResolvedValue({ ok: true, value: project() });

    render(<ProjectPanel />);
    await screen.findByTestId("empty-projects");

    fireEvent.change(screen.getByLabelText(/标题/), { target: { value: "雾都异闻" } });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    await waitFor(() => expect(createProject).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText(/标题/), { target: { value: "另一本书" } });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    await waitFor(() => expect(createProject).toHaveBeenCalledTimes(2));

    const [firstData, firstKey] = createProject.mock.calls[0];
    const [secondData, secondKey] = createProject.mock.calls[1];
    expect(firstData.get("title")).toBe("雾都异闻");
    expect(firstKey).toEqual(expect.any(String));
    // 成功之后是新的创建意图:换一个幂等键
    expect(secondKey).not.toBe(firstKey);
  });

  it("点击「详情」打开项目,再点「收起」关闭", async () => {
    listProjects.mockResolvedValue({ ok: true, value: [project()] });
    getProject.mockResolvedValue({ ok: true, value: project() });

    render(<ProjectPanel />);
    await screen.findByText("长夜灯");

    fireEvent.click(screen.getByRole("button", { name: "详情" }));
    const detail = await screen.findByTestId("project-detail");
    expect(getProject).toHaveBeenCalledWith(project().id);
    expect(detail).toHaveTextContent("一部志怪长篇");
    expect(detail).toHaveTextContent("zh-Hans");

    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    await waitFor(() => expect(screen.queryByTestId("project-detail")).not.toBeInTheDocument());
  });

  it("点击归档按钮调用 archiveProject 并刷新列表", async () => {
    listProjects
      .mockResolvedValueOnce({ ok: true, value: [project()] })
      .mockResolvedValueOnce({ ok: true, value: [project({ status: "archived" })] });
    archiveProject.mockResolvedValue({ ok: true, value: project({ status: "archived" }) });

    render(<ProjectPanel />);
    await screen.findByText("长夜灯");

    fireEvent.click(screen.getByRole("button", { name: "归档" }));

    await waitFor(() =>
      expect(archiveProject).toHaveBeenCalledWith("0199e0a2-0000-7000-8000-000000000001"),
    );
    await waitFor(() => expect(screen.getByTestId("status-archived")).toBeInTheDocument());
  });

  it("操作失败时显示中文错误提示,失败重试复用同一幂等键", async () => {
    createProject.mockResolvedValue({ ok: false, error: "请求校验失败(标题不能为空)" });

    render(<ProjectPanel />);
    await screen.findByTestId("empty-projects");

    fireEvent.change(screen.getByLabelText(/标题/), { target: { value: "重复之书" } });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    expect(await screen.findByTestId("action-error")).toHaveTextContent("请求校验失败");

    // 失败后不换键:同一意图的重试必须重放(ADR-0008)
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    await waitFor(() => expect(createProject).toHaveBeenCalledTimes(2));
    expect(createProject.mock.calls[0][1]).toBe(createProject.mock.calls[1][1]);
  });
});
