import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RevisionImageBuildsPanel } from "./RevisionImageBuildsPanel";

const FAILED_BUILD = {
  id: "build-failed-1",
  revision_id: "revision-new-22222222",
  generation: 3,
  source_commit: "commit-new",
  image_id: null,
  image_digest: null,
  base_image_id: "sha256:base",
  status: "failed",
  attempt: 1,
  queued_at: "2026-09-25T10:00:00Z",
  updated_at: "2026-09-25T10:01:00Z",
  failure_reason: "API_KEY=server-secret\nDocker build failed",
};

function buildList(items) {
  return { ok: true, status: 200, json: vi.fn().mockResolvedValue({ items, total: items.length }) };
}

function deferredResponse() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("RevisionImageBuildsPanel", () => {
  it("shows immutable provenance and bounded sanitized logs without rendering secrets", async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes("module_id=module-1")) return Promise.resolve(buildList([FAILED_BUILD]));
      if (String(url).includes("/build-failed-1/logs?offset=0&limit=16384")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: vi.fn().mockResolvedValue({
            text: "TOKEN=hunter2\nAuthorization: Bearer raw-bearer\nStep 4 failed",
            total_bytes: 99_999,
            next_offset: 16_384,
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<RevisionImageBuildsPanel active moduleId="module-1" />);

    expect(await screen.findByText("Generation 3")).toBeInTheDocument();
    expect(screen.getByText("revision-new-22222222")).toBeInTheDocument();
    expect(screen.getByText("sha256:base")).toBeInTheDocument();
    expect(screen.getByText(/Docker build failed/)).toBeInTheDocument();
    expect(screen.queryByText(/server-secret/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "View bounded log" }));

    const log = await screen.findByLabelText("Build log for build-failed-1");
    expect(log).toHaveTextContent("TOKEN=[REDACTED]");
    expect(log).toHaveTextContent("Authorization: [REDACTED]");
    expect(log).toHaveTextContent("Step 4 failed");
    expect(log).not.toHaveTextContent("hunter2");
    expect(log).not.toHaveTextContent("raw-bearer");
    expect(screen.getByText(/additional output is intentionally hidden/)).toBeInTheDocument();
  });

  it("retries once, disables all build actions in flight, and preserves failed state on backend error", async () => {
    let resolveRetry;
    const retryResponse = new Promise((resolve) => { resolveRetry = resolve; });
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("module_id=module-1")) return Promise.resolve(buildList([FAILED_BUILD]));
      if (String(url).endsWith("/build-failed-1/retry") && init?.method === "POST") return retryResponse;
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RevisionImageBuildsPanel active moduleId="module-1" />);

    const retryButton = await screen.findByRole("button", { name: "Retry failed build" });
    await userEvent.click(retryButton);

    await waitFor(() => expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/retry") && init?.method === "POST")).toHaveLength(1));
    expect(screen.getByRole("button", { name: "Retrying..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Rebuild all current revisions" })).toBeDisabled();

    resolveRetry({ ok: false, status: 409, json: vi.fn().mockResolvedValue({ error: "failed build is already queued" }) });

    expect(await screen.findByText(/failed build is already queued/)).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/retry") && init?.method === "POST")).toHaveLength(1);
  });

  it("queues rebuild-all once and shows the refreshed queued generation", async () => {
    let resolveRebuild;
    const rebuildResponse = new Promise((resolve) => { resolveRebuild = resolve; });
    let listCount = 0;
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("module_id=module-1")) {
        listCount += 1;
        return Promise.resolve(buildList(listCount === 1 ? [FAILED_BUILD] : [{ ...FAILED_BUILD, id: "build-queued-2", generation: 4, status: "queued", failure_reason: null }]));
      }
      if (String(url).endsWith("/revision-image-builds/rebuild-all") && init?.method === "POST") return rebuildResponse;
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RevisionImageBuildsPanel active moduleId="module-1" />);

    const rebuildButton = await screen.findByRole("button", { name: "Rebuild all current revisions" });
    await userEvent.click(rebuildButton);
    expect(screen.getByRole("button", { name: "Queuing rebuilds..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry failed build" })).toBeDisabled();
    resolveRebuild({ ok: true, status: 200, json: vi.fn().mockResolvedValue({ items: [], queued: 1 }) });

    expect(await screen.findByText("Generation 4")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/rebuild-all") && init?.method === "POST")).toHaveLength(1);
  });

  it("clears build A immediately when build B is selected and B fails", async () => {
    const buildA = { ...FAILED_BUILD, id: "build-a", generation: 2, failure_reason: null };
    const buildB = { ...FAILED_BUILD, id: "build-b", generation: 1, failure_reason: null };
    const buildBLog = deferredResponse();
    const fetchMock = vi.fn((url) => {
      if (String(url).includes("module_id=module-1")) return Promise.resolve(buildList([buildA, buildB]));
      if (String(url).includes("/build-a/logs?")) return Promise.resolve({
        ok: true,
        status: 200,
        json: vi.fn().mockResolvedValue({ text: "visible log from build A", total_bytes: 24, next_offset: null }),
      });
      if (String(url).includes("/build-b/logs?")) return buildBLog.promise;
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<RevisionImageBuildsPanel active moduleId="module-1" />);

    const buildCards = await screen.findAllByText(/Build build-/);
    const cardA = buildCards.find((node) => node.textContent === "Build build-a").closest("article");
    const cardB = buildCards.find((node) => node.textContent === "Build build-b").closest("article");
    await userEvent.click(within(cardA).getByRole("button", { name: "View bounded log" }));
    expect(await screen.findByText("visible log from build A")).toBeInTheDocument();

    await userEvent.click(within(cardB).getByRole("button", { name: "View bounded log" }));
    expect(screen.queryByText("visible log from build A")).not.toBeInTheDocument();

    await act(async () => {
      buildBLog.resolve({ ok: false, status: 500, json: vi.fn().mockResolvedValue({ error: "build B log unavailable" }) });
    });
    expect(await screen.findByText(/build B log unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("visible log from build A")).not.toBeInTheDocument();
  });

  it.each(["success", "error"])("ignores late build A log %s after selecting build B", async (lateOutcome) => {
    const buildA = { ...FAILED_BUILD, id: "build-a", generation: 2, failure_reason: null };
    const buildB = { ...FAILED_BUILD, id: "build-b", generation: 1, failure_reason: null };
    const buildALog = deferredResponse();
    const buildBLog = deferredResponse();
    const fetchMock = vi.fn((url) => {
      if (String(url).includes("module_id=module-1")) return Promise.resolve(buildList([buildA, buildB]));
      if (String(url).includes("/build-a/logs?")) return buildALog.promise;
      if (String(url).includes("/build-b/logs?")) return buildBLog.promise;
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<RevisionImageBuildsPanel active moduleId="module-1" />);

    const buildCards = await screen.findAllByText(/Build build-/);
    const cardA = buildCards.find((node) => node.textContent === "Build build-a").closest("article");
    const cardB = buildCards.find((node) => node.textContent === "Build build-b").closest("article");
    await userEvent.click(within(cardA).getByRole("button", { name: "View bounded log" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/build-a/logs?"))).toBe(true));
    await userEvent.click(within(cardB).getByRole("button", { name: "View bounded log" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/build-b/logs?"))).toBe(true));

    await act(async () => {
      if (lateOutcome === "success") {
        buildALog.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue({ text: "late build A output", total_bytes: 19, next_offset: null }) });
      } else {
        buildALog.reject(new Error("late build A error"));
      }
    });
    expect(screen.queryByText(/late build A/)).not.toBeInTheDocument();
    expect(screen.queryByText("Build log unavailable")).not.toBeInTheDocument();

    await act(async () => {
      buildBLog.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue({ text: "current build B output", total_bytes: 22, next_offset: null }) });
    });
    expect(await screen.findByText("current build B output")).toBeInTheDocument();
    expect(screen.queryByText(/late build A/)).not.toBeInTheDocument();
  });
  it("polls nonterminal builds, stops after terminal state, and cancels on unmount", async () => {
    vi.useFakeTimers();
    let listCount = 0;
    const fetchMock = vi.fn((url) => {
      if (!String(url).includes("module_id=module-1")) return Promise.reject(new Error(`Unexpected URL ${url}`));
      listCount += 1;
      const status = listCount === 1 ? "building" : "ready";
      return Promise.resolve(buildList([{ ...FAILED_BUILD, id: "build-live", status, failure_reason: null, updated_at: `update-${listCount}` }]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<RevisionImageBuildsPanel active moduleId="module-1" />);

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByText("building")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(screen.getByText("ready")).toBeInTheDocument();
    const terminalCount = listCount;
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(listCount).toBe(terminalCount);

    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(listCount).toBe(terminalCount);
  });
});
