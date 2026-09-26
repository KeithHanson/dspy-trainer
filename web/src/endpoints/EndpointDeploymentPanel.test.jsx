import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EndpointDeploymentPanel } from "./EndpointDeploymentPanel";

const jsonResponse = (body, status = 200) =>
  Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });

describe("EndpointDeploymentPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("shows mixed rollout and rollback state without exposing secrets", async () => {
    fetch.mockReturnValue(
      jsonResponse({
        phase: "rollback",
        migration_state: "rolling_back",
        legacy_fallback: false,
        active: {
          revision_id: "rev-old",
          build_id: "build-old",
          image_ref: "registry.example/worker@sha256:old",
        },
        target: {
          revision_id: "rev-new",
          build_id: "build-new",
          image_ref: "registry.example/worker@sha256:new",
        },
        previous: {
          revision_id: "rev-before",
          build_id: "build-before",
        },
        failed_target: {
          revision_id: "rev-new",
          build_id: "build-new",
          reason: "TOKEN=server-secret\nreadiness failed",
        },
        slots: [
          {
            slot: 0,
            lifecycle: "ready",
            ready: true,
            revision_id: "rev-old",
            build_id: "build-old",
            worker_id: "worker-old",
          },
          {
            slot: 1,
            lifecycle: "draining",
            draining: true,
            revision_id: "rev-new",
            build_id: "build-new",
            worker_id: "worker-new",
          },
        ],
        rollback_reason: "API_KEY=another-secret\nrollback requested",
      }),
    );

    render(<EndpointDeploymentPanel endpointId="endpoint-1" />);

    expect(await screen.findByText("Image rollout")).toBeInTheDocument();
    expect(screen.getByText("rollback")).toBeInTheDocument();
    expect(screen.getByText("rolling back")).toBeInTheDocument();
    expect(screen.getAllByText("rev-old").length).toBeGreaterThan(0);
    expect(screen.getAllByText("rev-new").length).toBeGreaterThan(0);
    expect(screen.getByText(/active capacity remains/i)).toBeInTheDocument();
    expect(screen.getByText(/mixed revisions/i)).toBeInTheDocument();
    expect(screen.getByText("draining")).toBeInTheDocument();
    expect(screen.getByText(/rollback requested/)).toHaveTextContent("[REDACTED]");
    expect(screen.queryByText(/server-secret|another-secret/)).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not report terminal historical slots as mixed serving versions", async () => {
    fetch.mockReturnValue(jsonResponse({
      phase: "ready",
      migration_state: "managed",
      legacy_fallback: false,
      desired_replica_count: 1,
      rollout_generation: 4,
      active: { revision_id: "rev-current", build_id: "build-current", build_status: "ready" },
      target: null,
      previous: { revision_id: "rev-old", build_id: "build-old", build_status: "ready" },
      failed_target: null,
      slots: [
        { container_id: "container-current", slot: 0, lifecycle: "ready", ready: true, draining: false, revision_id: "rev-current", build_id: "build-current", worker_id: "worker-current" },
        { container_id: "container-removed", slot: 0, lifecycle: "removed", ready: false, draining: false, revision_id: "rev-old", build_id: "build-old", worker_id: "worker-removed" },
        { container_id: "container-stopped", slot: 1, lifecycle: "stopped", ready: false, draining: false, revision_id: "rev-old", build_id: "build-old", worker_id: "worker-stopped" },
        { container_id: "container-failed", slot: 2, lifecycle: "failed", ready: false, draining: false, revision_id: "rev-broken", build_id: "build-broken", worker_id: "worker-failed" },
      ],
    }));

    render(<EndpointDeploymentPanel endpointId="endpoint-1" />);

    expect(await screen.findByText("Image rollout")).toBeInTheDocument();
    expect(screen.queryByText(/Mixed revisions or image builds/)).not.toBeInTheDocument();
    expect(screen.getByText(/1 of 1 desired slots ready/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("polls nonterminal rollouts until ready and cancels on unmount", async () => {
    vi.useFakeTimers();
    fetch
      .mockReturnValueOnce(
        jsonResponse({
          phase: "rolling",
          migration_state: "migrating",
          active: { revision_id: "rev-old", build_id: "build-old" },
          target: { revision_id: "rev-new", build_id: "build-new" },
          slots: [
            { slot: 0, lifecycle: "ready", revision_id: "rev-old", build_id: "build-old" },
            { slot: 1, lifecycle: "starting", revision_id: "rev-new", build_id: "build-new" },
          ],
        }),
      )
      .mockReturnValueOnce(
        jsonResponse({
          phase: "ready",
          migration_state: "complete",
          active: { revision_id: "rev-new", build_id: "build-new" },
          target: null,
          slots: [
            { slot: 0, lifecycle: "ready", revision_id: "rev-new", build_id: "build-new" },
            { slot: 1, lifecycle: "ready", revision_id: "rev-new", build_id: "build-new" },
          ],
        }),
      );

    const view = render(<EndpointDeploymentPanel endpointId="endpoint-1" />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("rolling")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getAllByText("ready").length).toBeGreaterThan(0);
    expect(fetch).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(fetch).toHaveBeenCalledTimes(2);

    view.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
