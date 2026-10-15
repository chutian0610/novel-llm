import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ServiceStatus } from "./service-status";

describe("ServiceStatus（服务状态）", () => {
  it("API 存活时显示「正常」与版本信息", () => {
    render(
      <ServiceStatus
        health={{ status: "ok" }}
        apiRoot={{ name: "novel-llm", version: "v1" }}
        error={null}
      />,
    );
    expect(screen.getByTestId("api-status")).toHaveTextContent("API 状态：正常");
    expect(screen.getByTestId("api-version")).toHaveTextContent("novel-llm v1");
  });

  it("健康检查异常时显示「异常」", () => {
    render(<ServiceStatus health={null} apiRoot={null} error={null} />);
    expect(screen.getByTestId("api-status")).toHaveTextContent("API 状态：异常");
  });

  it("连接失败时显示无法连接提示", () => {
    render(<ServiceStatus health={null} apiRoot={null} error="connect ECONNREFUSED" />);
    expect(screen.getByRole("alert")).toHaveTextContent("无法连接 API");
  });
});
