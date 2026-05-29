import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppShell } from "./AppShell";

describe("AppShell", () => {
  it("highlights active nav item", () => {
    render(
      <MemoryRouter initialEntries={["/runs"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const evalJobsLink = screen.getByRole("link", { name: "Eval Jobs" });
    expect(evalJobsLink).toHaveClass("shell-nav-item-active");
  });

  it("renders breadcrumb trail for route", () => {
    render(
      <MemoryRouter initialEntries={["/plans"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const breadcrumbNav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumbNav).toBeInTheDocument();
    expect(breadcrumbNav).toHaveTextContent("Default");
    expect(breadcrumbNav).toHaveTextContent("Evaluation Plans");
  });
});
