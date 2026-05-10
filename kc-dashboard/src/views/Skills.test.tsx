import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Skills from "./Skills";

vi.mock("../api/skills", () => ({
  listSkills: vi.fn().mockResolvedValue({
    skills: [
      { name: "hello", category: "greetings", description: "Greet politely.",
        version: null, platforms: null, tags: ["fun"], related_skills: [],
        skill_dir: "/x/hello" },
      { name: "github-auth", category: "github", description: "Set up GitHub auth.",
        version: "1.0.0", platforms: ["macos"], tags: [], related_skills: [],
        skill_dir: "/x/github-auth" },
    ],
  }),
  getSkill: vi.fn().mockResolvedValue({
    name: "hello", category: "greetings", description: "Greet politely.",
    version: null, platforms: null, tags: ["fun"], related_skills: [],
    skill_dir: "/x/hello",
    body: "# Hello\n\nFull instructions.",
    supporting_files: { references: [], templates: [], assets: [], scripts: [] },
  }),
  getSkillFile: vi.fn(),
}));


function renderView(initial = "/skills") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Skills />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}


describe("Skills view", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders skill rows from the API", async () => {
    renderView();
    await waitFor(() => expect(screen.getByText("hello")).toBeInTheDocument());
    expect(screen.getByText("github-auth")).toBeInTheDocument();
    expect(screen.getByText("Greet politely.")).toBeInTheDocument();
  });

  it("category filter chip narrows the list", async () => {
    renderView();
    await waitFor(() => screen.getByText("hello"));
    fireEvent.click(screen.getByRole("button", { name: /^github$/i }));
    expect(screen.queryByText("hello")).not.toBeInTheDocument();
    expect(screen.getByText("github-auth")).toBeInTheDocument();
  });

  it("search filters in-memory by name and description", async () => {
    renderView();
    await waitFor(() => screen.getByText("hello"));
    const search = screen.getByPlaceholderText(/search/i);
    fireEvent.change(search, { target: { value: "github" } });
    expect(screen.queryByText("hello")).not.toBeInTheDocument();
    expect(screen.getByText("github-auth")).toBeInTheDocument();
  });

  it("clicking a row expands the detail panel and fetches the body", async () => {
    const { getSkill } = await import("../api/skills");
    renderView();
    await waitFor(() => screen.getByText("hello"));
    fireEvent.click(screen.getByText("hello"));
    await waitFor(() => expect(getSkill).toHaveBeenCalledWith("hello"));
    await waitFor(() => expect(screen.getByText(/Full instructions/i)).toBeInTheDocument());
  });
});
