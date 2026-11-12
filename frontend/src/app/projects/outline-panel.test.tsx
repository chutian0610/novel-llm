import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getCurrentOutline, publishOutline } = vi.hoisted(() => ({
  getCurrentOutline: vi.fn(),
  publishOutline: vi.fn(),
}));

vi.mock("./outline", () => ({ getCurrentOutline, publishOutline }));

import { OutlinePanel } from "./outline-panel";
import type { OutlineView } from "./outline";

function outline(overrides: Partial<OutlineView> = {}): OutlineView {
  return {
    id: "0199e0a2-0000-7000-8000-0000000000c1",
    versionNumber: 1,
    payloadSchemaVersion: 1,
    synopsis: "少年执灯入山，照见百年前旧事。",
    constraints: ["全程志怪语感，不用现代词"],
    createdAt: "2026-11-12T08:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  getCurrentOutline.mockReset();
  publishOutline.mockReset();
});

describe("OutlinePanel(Outline 面板)", () => {
  it("已发布时显示当前版本、梗概与约束", async () => {
    getCurrentOutline.mockResolvedValue({ ok: true, value: outline() });
    render(<OutlinePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);

    await waitFor(() => expect(screen.getByTestId("outline-version")).toBeVisible());
    expect(screen.getByTestId("outline-version")).toHaveTextContent("第 1 版");
    expect(screen.getByText(/少年执灯入山/)).toBeVisible();
    expect(screen.getByText("全程志怪语感，不用现代词")).toBeVisible();
  });

  it("未发布时提示尚未发布", async () => {
    getCurrentOutline.mockResolvedValue({ ok: false, error: "尚未发布 Outline" });
    render(<OutlinePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);

    await waitFor(() => expect(screen.getByTestId("outline-empty")).toBeVisible());
  });

  it("发布提交梗概与逐行约束，并渲染返回的新版本", async () => {
    getCurrentOutline.mockResolvedValue({ ok: false, error: "尚未发布 Outline" });
    publishOutline.mockResolvedValue({
      ok: true,
      value: outline({
        versionNumber: 2,
        synopsis: "修订后的梗概。",
        constraints: ["志怪语感", "每章不超六千字"],
      }),
    });
    render(<OutlinePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByTestId("outline-empty")).toBeVisible());

    fireEvent.change(screen.getByLabelText(/工作梗概/), {
      target: { value: "修订后的梗概。" },
    });
    fireEvent.change(screen.getByLabelText(/约束/), {
      target: { value: "志怪语感\n每章不超六千字\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发布新版本" }));

    await waitFor(() => expect(publishOutline).toHaveBeenCalledTimes(1));
    expect(publishOutline.mock.calls[0][1]).toBe("修订后的梗概。");
    expect(publishOutline.mock.calls[0][2]).toEqual(["志怪语感", "每章不超六千字"]);
    await waitFor(() => expect(screen.getByTestId("outline-version")).toHaveTextContent("第 2 版"));
  });

  it("发布失败时显示动作错误", async () => {
    getCurrentOutline.mockResolvedValue({ ok: false, error: "尚未发布 Outline" });
    publishOutline.mockResolvedValue({ ok: false, error: "项目已归档" });
    render(<OutlinePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByTestId("outline-empty")).toBeVisible());

    fireEvent.change(screen.getByLabelText(/工作梗概/), { target: { value: "梗概" } });
    fireEvent.click(screen.getByRole("button", { name: "发布新版本" }));

    await waitFor(() =>
      expect(screen.getByTestId("outline-action-error")).toHaveTextContent("项目已归档"),
    );
  });
});
