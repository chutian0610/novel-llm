import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listProjects, createProject, archiveProject } = vi.hoisted(() => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  archiveProject: vi.fn(),
}));

vi.mock("./actions", () => ({ listProjects, createProject, archiveProject }));

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

  it("提交创建表单后调用 createProject 并刷新列表", async () => {
    createProject.mockResolvedValue({ ok: true, value: project() });

    render(<ProjectPanel />);
    await screen.findByTestId("empty-projects");

    fireEvent.change(screen.getByLabelText(/标题/), { target: { value: "雾都异闻" } });
    fireEvent.change(screen.getByLabelText(/描述/), { target: { value: "新的项目" } });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));

    await waitFor(() => expect(createProject).toHaveBeenCalledTimes(1));
    const formData = createProject.mock.calls[0][0] as FormData;
    expect(formData.get("title")).toBe("雾都异闻");
    expect(formData.get("description")).toBe("新的项目");
    await waitFor(() => expect(listProjects).toHaveBeenCalledTimes(2));
  });

  it("点击归档按钮调用 archiveProject 并刷新列表", async () => {
    listProjects
      .mockResolvedValueOnce({ ok: true, value: [project()] })
      .mockResolvedValueOnce({ ok: true, value: [project({ status: "archived" })] });
    archiveProject.mockResolvedValue({ ok: true, value: project({ status: "archived" }) });

    render(<ProjectPanel />);
    await screen.findByText("长夜灯");

    fireEvent.click(screen.getByRole("button", { name: "归档" }));

    await waitFor(() => expect(archiveProject).toHaveBeenCalledWith("0199e0a2-0000-7000-8000-000000000001"));
    await waitFor(() => expect(screen.getByTestId("status-archived")).toBeInTheDocument());
  });

  it("操作失败时显示中文错误提示", async () => {
    createProject.mockResolvedValue({ ok: false, error: "幂等键已被不同的请求使用,请重试" });

    render(<ProjectPanel />);
    await screen.findByTestId("empty-projects");

    fireEvent.change(screen.getByLabelText(/标题/), { target: { value: "重复之书" } });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));

    expect(await screen.findByTestId("action-error")).toHaveTextContent("幂等键已被不同的请求使用");
  });
});
