import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  getStructure,
  createVolume,
  createChapter,
  reorderVolumes,
  reorderChapters,
  moveChapter,
} = vi.hoisted(() => ({
  getStructure: vi.fn(),
  createVolume: vi.fn(),
  createChapter: vi.fn(),
  reorderVolumes: vi.fn(),
  reorderChapters: vi.fn(),
  moveChapter: vi.fn(),
}));

vi.mock("./structure", () => ({
  getStructure,
  createVolume,
  createChapter,
  reorderVolumes,
  reorderChapters,
  moveChapter,
}));

import { StructurePanel } from "./structure-panel";
import type { StructureView } from "./structure";

function structure(overrides: Partial<StructureView> = {}): StructureView {
  return {
    projectId: "0199e0a2-0000-7000-8000-000000000001",
    volumes: [
      {
        id: "0199e0a2-0000-7000-8000-0000000000a1",
        title: "第一卷",
        position: 1,
        chapters: [
          {
            id: "0199e0a2-0000-7000-8000-0000000000b1",
            title: "第一章",
            volumeId: "0199e0a2-0000-7000-8000-0000000000a1",
            position: 1,
          },
          {
            id: "0199e0a2-0000-7000-8000-0000000000b2",
            title: "第二章",
            volumeId: "0199e0a2-0000-7000-8000-0000000000a1",
            position: 2,
          },
        ],
      },
      {
        id: "0199e0a2-0000-7000-8000-0000000000a2",
        title: "第二卷",
        position: 2,
        chapters: [],
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  getStructure.mockReset().mockResolvedValue({ ok: true, value: structure() });
  createVolume.mockReset();
  createChapter.mockReset();
  reorderVolumes.mockReset();
  reorderChapters.mockReset();
  moveChapter.mockReset();
});

describe("StructurePanel(结构面板)", () => {
  it("载入后按位置渲染卷与章节树", async () => {
    render(<StructurePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);

    await waitFor(() => expect(screen.getByText("1. 第一卷")).toBeVisible());
    expect(screen.getByText("2. 第二卷")).toBeVisible();
    expect(screen.getByText("1. 第一章")).toBeVisible();
    expect(screen.getByText("2. 第二章")).toBeVisible();
  });

  it("上移第一卷用全序重排，并渲染返回的新结构", async () => {
    reorderVolumes.mockResolvedValue({
      ok: true,
      value: structure({
        volumes: [
          { ...structure().volumes[1], position: 1 },
          { ...structure().volumes[0], position: 2 },
        ],
      }),
    });
    render(<StructurePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByText("1. 第一卷")).toBeVisible());

    fireEvent.click(screen.getByRole("button", { name: "下移 第一卷" }));

    await waitFor(() =>
      expect(reorderVolumes).toHaveBeenCalledWith(
        "0199e0a2-0000-7000-8000-000000000001",
        [
          "0199e0a2-0000-7000-8000-0000000000a2",
          "0199e0a2-0000-7000-8000-0000000000a1",
        ],
      ),
    );
    await waitFor(() => expect(screen.getByText("1. 第二卷")).toBeVisible());
  });

  it("上移第一章按卷内全序重排", async () => {
    reorderChapters.mockResolvedValue({ ok: true, value: structure() });
    render(<StructurePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByText("2. 第二章")).toBeVisible());

    fireEvent.click(screen.getByRole("button", { name: "上移 第二章" }));

    await waitFor(() =>
      expect(reorderChapters).toHaveBeenCalledWith(
        "0199e0a2-0000-7000-8000-000000000001",
        "0199e0a2-0000-7000-8000-0000000000a1",
        [
          "0199e0a2-0000-7000-8000-0000000000b2",
          "0199e0a2-0000-7000-8000-0000000000b1",
        ],
      ),
    );
  });

  it("移动章节到另一卷走 move 动作", async () => {
    moveChapter.mockResolvedValue({ ok: true, value: structure() });
    render(<StructurePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByText("1. 第一章")).toBeVisible());

    fireEvent.change(screen.getByRole("combobox", { name: "移动 第一章 到" }), {
      target: { value: "0199e0a2-0000-7000-8000-0000000000a2" },
    });

    await waitFor(() =>
      expect(moveChapter).toHaveBeenCalledWith(
        "0199e0a2-0000-7000-8000-000000000001",
        "0199e0a2-0000-7000-8000-0000000000b1",
        "0199e0a2-0000-7000-8000-0000000000a2",
      ),
    );
  });

  it("动作失败时显示稳定错误码对应的中文提示", async () => {
    reorderVolumes.mockResolvedValue({ ok: false, error: "重排顺序与当前结构不符" });
    render(<StructurePanel projectId="0199e0a2-0000-7000-8000-000000000001" />);
    await waitFor(() => expect(screen.getByText("1. 第一卷")).toBeVisible());

    fireEvent.click(screen.getByRole("button", { name: "下移 第一卷" }));

    await waitFor(() =>
      expect(screen.getByTestId("structure-action-error")).toHaveTextContent(
        "重排顺序与当前结构不符",
      ),
    );
  });
});
