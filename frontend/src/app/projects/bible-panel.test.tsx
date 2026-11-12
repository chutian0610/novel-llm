import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  getBibleDraft,
  getCurrentBible,
  createBibleIdentity,
  createBibleEntry,
  archiveBibleEntry,
  publishBible,
  updateBibleEntry,
} = vi.hoisted(() => ({
  getBibleDraft: vi.fn(),
  getCurrentBible: vi.fn(),
  createBibleIdentity: vi.fn(),
  createBibleEntry: vi.fn(),
  archiveBibleEntry: vi.fn(),
  publishBible: vi.fn(),
  updateBibleEntry: vi.fn(),
}));

vi.mock("./bible", () => ({
  getBibleDraft,
  getCurrentBible,
  createBibleIdentity,
  createBibleEntry,
  archiveBibleEntry,
  publishBible,
  updateBibleEntry,
}));

function updateEntryMock() {
  updateBibleEntry.mockReset().mockResolvedValue({ ok: true, value: draft() });
}
function updateEntrySpied() {
  return updateBibleEntry;
}

import { BiblePanel } from "./bible-panel";
import type { BibleDraftView } from "./bible";

const LIN = "0199e0a2-0000-7000-8000-0000000000d1";

function draft(overrides: Partial<BibleDraftView> = {}): BibleDraftView {
  return {
    projectId: "0199e0a2-0000-7000-8000-000000000001",
    identities: [{ id: LIN, name: "林灯", aliases: ["阿灯"], revision: 1 }],
    entries: [
      {
        id: "0199e0a2-0000-7000-8000-0000000000e1",
        identityId: LIN,
        entryType: "character",
        detail: { description: "执灯的少年。", role: null },
        status: "active",
        revision: 1,
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  getBibleDraft.mockReset().mockResolvedValue({ ok: true, value: draft() });
  getCurrentBible.mockReset().mockResolvedValue({ ok: true, value: null });
  createBibleIdentity.mockReset();
  createBibleEntry.mockReset();
  archiveBibleEntry.mockReset();
  publishBible.mockReset();
  updateBibleEntry.mockReset();
});

describe("BiblePanel(Bible 面板)", () => {
  it("未发布时提示发布语义，草稿条目按类型渲染", async () => {
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);

    await waitFor(() => expect(screen.getByTestId("bible-empty")).toBeVisible());
    expect(screen.getByTestId("bible-entries")).toHaveTextContent("【角色】执灯的少年。");
  });

  it("已发布时显示当前版本与冻结规模", async () => {
    getCurrentBible.mockResolvedValue({
      ok: true,
      value: { versionNumber: 2, payloadSchemaVersion: 1, identityCount: 3, entryCount: 5 },
    });
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);

    await waitFor(() =>
      expect(screen.getByTestId("bible-version")).toHaveTextContent("第 2 版"),
    );
    expect(screen.getByTestId("bible-version")).toHaveTextContent("3 个身份");
    expect(screen.getByTestId("bible-version")).toHaveTextContent("5 条冻结条目");
  });

  it("新建身份提交名称与逐个别名", async () => {
    createBibleIdentity.mockResolvedValue({ ok: true, value: draft() });
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByText("新建身份")).toBeVisible());

    fireEvent.change(screen.getByLabelText(/身份名/), { target: { value: "陈守一" } });
    fireEvent.change(screen.getByLabelText(/别名/), { target: { value: "守一, 老陈" } });
    fireEvent.click(screen.getByRole("button", { name: "新建身份" }));

    await waitFor(() => expect(createBibleIdentity).toHaveBeenCalledTimes(1));
    expect(createBibleIdentity.mock.calls[0][1]).toBe("陈守一");
    expect(createBibleIdentity.mock.calls[0][2]).toEqual(["守一", "老陈"]);
  });

  it("新建条目把主字段映射进该类型的 detail", async () => {
    createBibleEntry.mockResolvedValue({ ok: true, value: draft() });
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByLabelText(/条目类型/)).toBeVisible());

    fireEvent.change(screen.getByLabelText(/所属身份/), { target: { value: LIN } });
    fireEvent.change(screen.getByLabelText(/条目类型/), { target: { value: "open_question" } });
    fireEvent.change(screen.getByLabelText(/内容/), { target: { value: "灯芯的裂痕是谁刻的？" } });
    fireEvent.click(screen.getByRole("button", { name: "新建条目" }));

    await waitFor(() => expect(createBibleEntry).toHaveBeenCalledTimes(1));
    expect(createBibleEntry.mock.calls[0][2]).toBe("open_question");
    expect(createBibleEntry.mock.calls[0][3]).toEqual({ question: "灯芯的裂痕是谁刻的？" });
  });

  it("归档条目带上该条目的 If-Match 版本", async () => {
    archiveBibleEntry.mockResolvedValue({
      ok: true,
      value: draft({ entries: [] }),
    });
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByTestId("bible-entries")).toBeVisible());

    fireEvent.click(screen.getByRole("button", { name: "归档 角色 条目" }));

    await waitFor(() => expect(archiveBibleEntry).toHaveBeenCalledTimes(1));
    expect(archiveBibleEntry.mock.calls[0][0]).toBe("0199e0a2-0000-7000-8000-000000000001");
    expect(archiveBibleEntry.mock.calls[0][1].revision).toBe(1);
    await waitFor(() => expect(screen.queryByTestId("bible-entries")).toBeNull());
  });

  it("发布调用 publish 并渲染新版本号", async () => {
    // 载入时尚未发布；发布后的 reload 里则已是第 1 版。
    getCurrentBible.mockResolvedValueOnce({ ok: true, value: null });
    getCurrentBible.mockResolvedValue({
      ok: true,
      value: {
        versionNumber: 1,
        payloadSchemaVersion: 1,
        entries: [{ name: "林灯", aliases: [], entryType: "character" }],
      },
    });
    publishBible.mockResolvedValue({
      ok: true,
      value: { versionNumber: 1, payloadSchemaVersion: 1, identityCount: 1, entryCount: 1 },
    });
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByTestId("publish-bible")).toBeVisible());

    fireEvent.click(screen.getByTestId("publish-bible"));

    await waitFor(() => expect(publishBible).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("bible-version")).toHaveTextContent("第 1 版"),
    );
  });

  it("动作失败时显示稳定错误码对应的中文提示", async () => {
    publishBible.mockResolvedValue({ ok: false, error: "项目已归档" });
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByTestId("publish-bible")).toBeVisible());

    fireEvent.click(screen.getByTestId("publish-bible"));

    await waitFor(() =>
      expect(screen.getByTestId("bible-action-error")).toHaveTextContent("项目已归档"),
    );
  });
});

describe("BiblePanel(评审修复钉子)", () => {
  it("关系条目把从/到身份一并送进 detail", async () => {
    const CHEN = "0199e0a2-0000-7000-8000-0000000000d2";
    const twoIdentities = draft({
      identities: [
        { id: LIN, name: "林灯", aliases: [], revision: 1 },
        { id: CHEN, name: "陈守一", aliases: [], revision: 1 },
      ],
    });
    getBibleDraft.mockResolvedValue({ ok: true, value: twoIdentities });
    createBibleEntry.mockResolvedValue({ ok: true, value: twoIdentities });
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByLabelText(/关系从/)).toBeVisible());

    fireEvent.change(screen.getByLabelText(/所属身份/), { target: { value: LIN } });
    fireEvent.change(screen.getByLabelText(/条目类型/), { target: { value: "relationship" } });
    fireEvent.change(screen.getByLabelText(/内容/), { target: { value: "师徒" } });
    fireEvent.change(screen.getByLabelText(/关系从/), { target: { value: LIN } });
    fireEvent.change(screen.getByLabelText(/关系到/), { target: { value: CHEN } });
    fireEvent.click(screen.getByRole("button", { name: "新建条目" }));

    await waitFor(() => expect(createBibleEntry).toHaveBeenCalledTimes(1));
    expect(createBibleEntry.mock.calls[0][3]).toEqual({
      description: "师徒",
      from_identity: LIN,
      to_identity: CHEN,
    });
  });

  it("条目内联编辑把主字段合并进原 detail 后保存", async () => {
    updateEntryMock();
    render(<BiblePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByTestId("bible-entries")).toBeVisible());

    fireEvent.click(screen.getByRole("button", { name: "编辑 角色 条目" }));
    fireEvent.change(screen.getByLabelText("编辑 角色 条目内容"), {
      target: { value: "改后的描述" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(updateEntrySpied()).toHaveBeenCalledTimes(1));
    expect(updateEntrySpied().mock.calls[0][2]).toEqual({
      description: "改后的描述",
      role: null,
    });
  });
});
