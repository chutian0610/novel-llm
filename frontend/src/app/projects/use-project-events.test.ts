import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventSourceFactory, useProjectEvents } from "./use-project-events";

function envelope(eventId: string, eventType: string, title: string) {
  return {
    envelope_version: 1,
    event_id: eventId,
    cursor: eventId,
    occurred_at: "2026-11-05T10:00:00Z",
    event_type: eventType,
    data: { title },
  };
}

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  fail() {
    this.onerror?.();
  }
}

const urlFactory = (): EventSourceFactory => (url) => new FakeEventSource(url);

beforeEach(() => {
  FakeEventSource.instances = [];
});
afterEach(() => {
  vi.useRealTimers();
});

describe("useProjectEvents(项目事件流)", () => {
  it("接收事件并按 event id 去重(at-least-once 投递)", async () => {
    const { result } = renderHook(() => useProjectEvents("p1", urlFactory()));

    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/api/projects/p1/events");

    act(() => {
      source.onopen?.();
      source.emit(envelope("e1", "project.created", "长夜灯"));
      source.emit(envelope("e1", "project.created", "长夜灯")); // duplicate
      source.emit(envelope("e2", "project.updated", "长夜灯(修)"));
    });

    expect(result.current.connection).toBe("live");
    expect(result.current.events.map((event) => event.eventId)).toEqual(["e1", "e2"]);
    expect(result.current.events[0].eventType).toBe("project.created");
  });

  it("断线后带最后游标重连,恢复恰好错过的事件", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useProjectEvents("p1", urlFactory()));

    await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const first = FakeEventSource.instances[0];

    act(() => {
      first.onopen?.();
      first.emit(envelope("e1", "project.created", "长夜灯"));
    });

    act(() => first.fail());
    expect(result.current.connection).toBe("reconnecting");
    expect(first.closed).toBe(true);

    // Backoff passes → reconnects with the cursor of the last received event.
    act(() => vi.advanceTimersByTime(500));
    await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(2));
    const second = FakeEventSource.instances[1];
    expect(second.url).toBe("/api/projects/p1/events?cursor=e1");
    expect(result.current.connection).toBe("reconnecting");

    act(() => {
      second.onopen?.();
      second.emit(envelope("e2", "project.archived", "长夜灯"));
    });

    expect(result.current.connection).toBe("live");
    expect(result.current.events.map((event) => event.eventId)).toEqual(["e1", "e2"]);
  });

  it("卸载时关闭连接,不再重连", async () => {
    const { unmount } = renderHook(() => useProjectEvents("p1", urlFactory()));
    await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const source = FakeEventSource.instances[0];

    unmount();
    expect(source.closed).toBe(true);

    act(() => source.fail());
    expect(FakeEventSource.instances.length).toBe(1); // no reconnect after unmount
  });

  it("EventSource 不可用时显示不可连接状态", () => {
    const failing: EventSourceFactory = () => {
      throw new Error("no EventSource");
    };
    const { result } = renderHook(() => useProjectEvents("p1", failing));
    expect(result.current.connection).toBe("unavailable");
    expect(result.current.events).toEqual([]);
  });

  it("未知信封版本被丢弃,不当作已理解渲染", async () => {
    const { result } = renderHook(() => useProjectEvents("p1", urlFactory()));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const source = FakeEventSource.instances[0];

    act(() => {
      source.onopen?.();
      source.emit({ ...envelope("f1", "project.created", "未来之书"), envelope_version: 2 });
      source.emit(envelope("f2", "project.created", "当前之书"));
    });

    expect(result.current.events.map((event) => event.eventId)).toEqual(["f2"]);
  });

  it("无法解析的消息被忽略", async () => {
    const { result } = renderHook(() => useProjectEvents("p1", urlFactory()));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const source = FakeEventSource.instances[0];

    act(() => {
      source.onopen?.();
      source.onmessage?.({ data: "not-json" } as MessageEvent);
      source.emit(envelope("g1", "project.created", "长夜灯"));
    });

    expect(result.current.events.map((event) => event.eventId)).toEqual(["g1"]);
  });
});
