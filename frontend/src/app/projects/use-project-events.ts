"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * One durable event in its versioned envelope (ADR-0002/0003). Delivery is
 * at-least-once, so consumers deduplicate by event id.
 */
export interface EventEnvelopeView {
  envelopeVersion: number;
  eventId: string;
  cursor: string;
  occurredAt: string;
  eventType: string;
  data: Record<string, unknown>;
}

export type ConnectionState = "connecting" | "live" | "reconnecting" | "unavailable";

interface MinimalEventSource {
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: (() => void) | null;
}

export type EventSourceFactory = (url: string) => MinimalEventSource;

function defaultEventSourceFactory(url: string): MinimalEventSource {
  if (typeof EventSource === "undefined") {
    throw new Error("EventSource is not available in this environment");
  }
  return new EventSource(url) as unknown as MinimalEventSource;
}

const RECONNECT_DELAY_MS = 500;

/**
 * Follows one Project's durable event stream: replays after a disconnect with
 * the last received cursor (exactly the missed events), dedupes by event id
 * (at-least-once delivery), and reconnects with a short backoff.
 */
export function useProjectEvents(
  projectId: string | null,
  factory: EventSourceFactory = defaultEventSourceFactory,
): { events: EventEnvelopeView[]; connection: ConnectionState } {
  const [events, setEvents] = useState<EventEnvelopeView[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  // Latest-ref for the factory: a new factory identity per render must not
  // retrigger the connection; only a new projectId does.
  const factoryRef = useRef(factory);
  factoryRef.current = factory;
  const seenIdsRef = useRef<Set<string>>(new Set());
  const lastCursorRef = useRef<string | null>(null);
  const sourceRef = useRef<MinimalEventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(
    (cursor: string | null) => {
      if (projectId === null) {
        return;
      }
      const suffix = cursor === null ? "" : `?cursor=${encodeURIComponent(cursor)}`;
      let source: MinimalEventSource;
      try {
        source = factoryRef.current(`/api/projects/${projectId}/events${suffix}`);
      } catch {
        setConnection("unavailable");
        return;
      }
      sourceRef.current = source;
      source.onopen = () => setConnection("live");
      source.onmessage = (message: MessageEvent) => {
        let envelope: EventEnvelopeView;
        try {
          // The wire shape is the versioned envelope (snake_case); map it to
          // the view model explicitly so the two can evolve independently.
          const wire = JSON.parse(message.data) as {
            envelope_version: number;
            event_id: string;
            cursor: string;
            occurred_at: string;
            event_type: string;
            data: Record<string, unknown>;
          };
          envelope = {
            envelopeVersion: wire.envelope_version,
            eventId: wire.event_id,
            cursor: wire.cursor,
            occurredAt: wire.occurred_at,
            eventType: wire.event_type,
            data: wire.data,
          };
        } catch {
          return;
        }
        if (seenIdsRef.current.has(envelope.eventId)) {
          return; // at-least-once delivery: dedupe by event id
        }
        seenIdsRef.current.add(envelope.eventId);
        lastCursorRef.current = envelope.cursor;
        setEvents((previous) => [...previous, envelope]);
      };
      source.onerror = () => {
        source.close();
        sourceRef.current = null;
        setConnection("reconnecting");
        // Reconnect from the last received cursor: the server replays
        // exactly what was missed while we were away.
        reconnectTimerRef.current = setTimeout(
          () => connect(lastCursorRef.current),
          RECONNECT_DELAY_MS,
        );
      };
    },
    [projectId],
  );

  useEffect(() => {
    seenIdsRef.current = new Set();
    lastCursorRef.current = null;
    setEvents([]);
    setConnection("connecting");
    connect(null);
    return () => {
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [connect]);

  return { events, connection };
}
