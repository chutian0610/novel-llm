import type { NextRequest } from "next/server";

import { API_BASE_URL } from "@/lib/api/client";

export const dynamic = "force-dynamic";

/**
 * SSE proxy: the browser never reaches the FastAPI service directly (only the
 * web interface is published to 127.0.0.1, ADR-0005), so the workbench proxies
 * the durable event stream through Next.js. The upstream is the source of
 * truth — headers, status, and the byte stream pass through untouched.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  const { projectId } = await params;
  const cursor = request.nextUrl.searchParams.get("cursor");
  const upstream = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/events/stream${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
    { headers: { accept: "text/event-stream" } },
  );

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "text/event-stream",
      "cache-control": "no-cache",
    },
  });
}
