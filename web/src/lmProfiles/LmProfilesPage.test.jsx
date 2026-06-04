import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { LmProfileEditorPage, LmProfilesPage } from "./LmProfilesPage";

describe("LmProfilesPage", () => {
  it("renders profiles from API", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "lm-1",
              name: "GPT-4o Baseline",
              model: "openai/gpt-4o",
              api_base: "http://litellm:4000",
              model_type: "responses",
              default_params: { temperature: 0 },
              lm_class_path: "dspy.LM",
              virtual_key: "sk-very-secret-key",
              updated_at: "2026-01-01T00:00:00+00:00",
            },
          ]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <LmProfilesPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("GPT-4o Baseline")).toBeInTheDocument();
    const profileCard = screen.getByText("GPT-4o Baseline").closest("article");
    expect(profileCard).toBeTruthy();
    expect(within(profileCard).getByText("responses")).toBeInTheDocument();
    expect(within(profileCard).getByText("http://litellm:4000")).toBeInTheDocument();
    expect(within(profileCard).getByText("Test with curl")).toBeInTheDocument();
    expect(within(profileCard).getByText(/sk-very-secret-key/)).toBeInTheDocument();
    expect(within(profileCard).getByText(/"model": "lm-profile:lm-1"/)).toBeInTheDocument();
    expect(within(profileCard).getByRole("button", { name: "Copy curl" })).toBeInTheDocument();
  });

  it("shows list without inline editor", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <LmProfilesPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("No LM profiles")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save profile" })).not.toBeInTheDocument();
  });

  it("creates a profile from editor page", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/lm-profiles") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "lm-2" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <LmProfileEditorPage />
      </MemoryRouter>,
    );

    await userEvent.type(await screen.findByLabelText("Name"), "Reasoning stable");
    await userEvent.clear(screen.getByLabelText("Model"));
    await userEvent.type(screen.getByLabelText("Model"), "openai/o3");
    await userEvent.clear(screen.getByLabelText("API base"));
    await userEvent.type(screen.getByLabelText("API base"), "http://litellm:4000");
    await userEvent.selectOptions(screen.getByLabelText("Model type"), "responses");
    await userEvent.type(screen.getByLabelText("Upstream API key (sent to LiteLLM only)"), "sk-upstream");
    fireEvent.change(screen.getByLabelText("Default params (JSON object)"), { target: { value: '{"temperature":0.1}' } });
    await userEvent.click(screen.getByRole("button", { name: "Save profile" }));

    const createCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/lm-profiles") && init?.method === "POST");
    expect(createCall).toBeTruthy();
    expect(JSON.parse(createCall[1].body)).toMatchObject({
      name: "Reasoning stable",
      model: "openai/o3",
      api_base: "http://litellm:4000",
      model_type: "responses",
      upstream_api_key: "sk-upstream",
    });
  });

  it("prefills new editor with LiteLLM-friendly defaults", async () => {
    render(
      <MemoryRouter>
        <LmProfileEditorPage />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText("Model")).toHaveValue("openai/gpt-4o-mini");
    expect(screen.getByLabelText("API base")).toHaveValue("");
    expect(screen.getByLabelText("Model type")).toHaveValue("responses");
    expect(screen.getByLabelText("Upstream API key (sent to LiteLLM only)")).toHaveValue("");
    expect(screen.getByLabelText("Default params (JSON object)").value).toContain("temperature");
    expect(screen.getByLabelText("Default params (JSON object)").value).toContain("max_tokens");
  });

  it("requires upstream api key on create", async () => {
    const fetchMock = vi.fn(() => Promise.reject(new Error("should not call fetch")));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <LmProfileEditorPage />
      </MemoryRouter>,
    );

    await userEvent.type(await screen.findByLabelText("Name"), "No key");
    await userEvent.clear(screen.getByLabelText("Model"));
    await userEvent.type(screen.getByLabelText("Model"), "openai/o3");
    await userEvent.type(screen.getByLabelText("API base"), "http://litellm:4000");
    await userEvent.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByText("Upstream API key is required when creating a profile.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows and rotates virtual key on edit page", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/lm-profiles/lm-1") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "lm-1",
            name: "Profile",
            model: "openai/o3",
            api_base: "http://litellm:4000",
            model_type: "responses",
            default_params: {},
            virtual_key: "vk-old",
          }),
        });
      }
      if (String(url).endsWith("/lm-profiles/lm-1/rotate-key") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ virtual_key: "vk-new" }) });
      }
      if (String(url).endsWith("/lm-profiles/lm-1/test-connection") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ ok: true, reply: "connection-ok" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/lm-profiles/lm-1/edit"]}>
        <Routes>
          <Route path="/lm-profiles/:profileId/edit" element={<LmProfileEditorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("vk-old")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Connection succeeded")).toBeInTheDocument();
    expect(screen.getByText(/connection-ok/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Rotate key" }));
    expect(await screen.findByText("vk-new")).toBeInTheDocument();
  });
});
