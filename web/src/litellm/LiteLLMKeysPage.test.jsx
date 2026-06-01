import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { LiteLLMKeysPage } from "./LiteLLMKeysPage";

describe("LiteLLMKeysPage", () => {
  it("lists keys", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/litellm/keys") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ keys: [{ key: "sk-1", key_alias: "primary", models: ["openai/gpt-4o-mini"], blocked: false }] }) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryRouter><LiteLLMKeysPage /></MemoryRouter>);
    expect(await screen.findByText("primary")).toBeInTheDocument();
  });

  it("creates a key seeded from lm profile model", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/litellm/keys") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ keys: [] }) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o", model: "openai/gpt-4o" }]) });
      }
      if (String(url).endsWith("/litellm/keys") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ key: "sk-1" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryRouter><LiteLLMKeysPage /></MemoryRouter>);

    await userEvent.selectOptions(await screen.findByLabelText("Seed from LM profile (optional)"), "lm-1");
    await userEvent.type(screen.getByLabelText("Key alias (optional)"), "lm-key");
    fireEvent.change(screen.getByLabelText("Metadata (JSON object)"), { target: { value: '{"owner":"platform"}' } });
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));

    const createCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/litellm/keys") && init?.method === "POST");
    expect(createCall).toBeTruthy();
    const payload = JSON.parse(createCall[1].body);
    expect(payload.models).toContain("openai/gpt-4o");
    expect(payload.key_alias).toBe("lm-key");
  });

  it("updates key metadata and limits", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/litellm/keys") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({ keys: [{ key: "sk-1", key_alias: "primary", models: ["openai/gpt-4o-mini"], aliases: { default: "openai/gpt-4o-mini" }, metadata: { owner: "team" }, blocked: false }] }),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/litellm/keys/sk-1") && init?.method === "PATCH") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ key: "sk-1" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryRouter><LiteLLMKeysPage /></MemoryRouter>);

    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Edit metadata (JSON object)"), { target: { value: '{"owner":"platform"}' } });
    await userEvent.clear(screen.getByLabelText("Edit models (comma-separated)"));
    await userEvent.type(screen.getByLabelText("Edit models (comma-separated)"), "openai/gpt-4o-mini, openai/o3");
    await userEvent.clear(screen.getByLabelText("Default alias model"));
    await userEvent.type(screen.getByLabelText("Default alias model"), "openai/o3");
    await userEvent.type(screen.getByLabelText("RPM limit"), "120");
    await userEvent.type(screen.getByLabelText("TPM limit"), "40000");
    await userEvent.type(screen.getByLabelText("Max budget"), "5.5");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    const patchCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/litellm/keys/sk-1") && init?.method === "PATCH");
    expect(patchCall).toBeTruthy();
    expect(JSON.parse(patchCall[1].body)).toMatchObject({
      key: "sk-1",
      models: ["openai/gpt-4o-mini", "openai/o3"],
      aliases: { default: "openai/o3" },
      metadata: { owner: "platform" },
      rpm_limit: 120,
      tpm_limit: 40000,
      max_budget: 5.5,
    });
  });
});
