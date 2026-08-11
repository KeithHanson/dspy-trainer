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
              api_base: "https://api.openai.com",
              model_type: "responses",
              default_params: { temperature: 0 },
              lm_class_path: "dspy.LM",
              has_api_key: true,
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
    expect(within(profileCard).getByText("https://api.openai.com")).toBeInTheDocument();
    expect(within(profileCard).getByText("Stored API key configured")).toBeInTheDocument();
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
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "lm-2", has_api_key: true }) });
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
    await userEvent.type(screen.getByLabelText("API base"), "https://api.openai.com");
    await userEvent.selectOptions(screen.getByLabelText("Model type"), "responses");
    await userEvent.type(screen.getByLabelText("Provider API key (optional)"), "sk-provider");
    fireEvent.change(screen.getByLabelText("Default params (JSON object)"), { target: { value: '{"temperature":0.1}' } });
    await userEvent.click(screen.getByRole("button", { name: "Save profile" }));

    const createCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/lm-profiles") && init?.method === "POST");
    expect(createCall).toBeTruthy();
    expect(JSON.parse(createCall[1].body)).toMatchObject({
      name: "Reasoning stable",
      model: "openai/o3",
      api_base: "https://api.openai.com",
      model_type: "responses",
      api_key: "sk-provider",
    });
  });

  it("prefills new editor with direct-provider defaults", async () => {
    render(
      <MemoryRouter>
        <LmProfileEditorPage />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText("Model")).toHaveValue("openai/gpt-4o-mini");
    expect(screen.getByLabelText("API base")).toHaveValue("");
    expect(screen.getByLabelText("Model type")).toHaveValue("responses");
    expect(screen.getByLabelText("Provider API key (optional)")).toHaveValue("");
    expect(screen.getByLabelText("Default params (JSON object)").value).toContain("temperature");
    expect(screen.getByLabelText("Default params (JSON object)").value).toContain("max_tokens");
  });

  it("shows and tests stored credentials on edit page", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/lm-profiles/lm-1") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "lm-1",
            name: "Profile",
            model: "openai/o3",
            api_base: "https://api.openai.com",
            model_type: "responses",
            default_params: {},
            has_api_key: true,
          }),
        });
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

    expect(await screen.findByText("Provider API key stored.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Connection succeeded")).toBeInTheDocument();
    expect(screen.getByText(/connection-ok/)).toBeInTheDocument();
  });
});
