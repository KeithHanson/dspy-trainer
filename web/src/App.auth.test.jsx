import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { App } from "./App";

const mockUseAuth0 = vi.fn();

vi.mock("@auth0/auth0-react", () => ({
  useAuth0: () => mockUseAuth0(),
}));

describe("App auth gating", () => {
  it("shows auth screen when unauthenticated", () => {
    mockUseAuth0.mockReturnValue({
      error: undefined,
      isAuthenticated: false,
      isLoading: false,
      loginWithRedirect: vi.fn(),
      logout: vi.fn(),
      user: null,
    });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByText("Sign in to your workspace")).toBeInTheDocument();
  });

  it("dispatches Auth0 login on provider click", async () => {
    const loginWithRedirect = vi.fn();
    mockUseAuth0.mockReturnValue({
      error: undefined,
      isAuthenticated: false,
      isLoading: false,
      loginWithRedirect,
      logout: vi.fn(),
      user: null,
    });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Continue with GitHub" }));
    expect(loginWithRedirect).toHaveBeenCalledTimes(1);
    expect(loginWithRedirect).toHaveBeenCalledWith(
      expect.objectContaining({
        authorizationParams: expect.any(Object),
      }),
    );
  });

  it("shows shell routes when authenticated", async () => {
    mockUseAuth0.mockReturnValue({
      error: undefined,
      isAuthenticated: true,
      isLoading: false,
      loginWithRedirect: vi.fn(),
      logout: vi.fn(),
      user: { name: "Test User", email: "test@example.com" },
    });

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByText("Good morning, Test")).toBeInTheDocument();
  });
});
