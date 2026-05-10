import { describe, it, expect, vi, beforeEach } from "vitest";
import { listSkills, getSkill, getSkillFile } from "./skills";


describe("skills api", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("listSkills issues GET /skills", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ skills: [] }), { status: 200 }) as Response,
    );
    await listSkills();
    expect(fetchMock.mock.calls[0][0]).toContain("/skills");
  });

  it("getSkill issues GET /skills/{name}", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ name: "hello" }), { status: 200 }) as Response,
    );
    await getSkill("hello");
    expect(fetchMock.mock.calls[0][0]).toContain("/skills/hello");
  });

  it("getSkillFile encodes the path", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ content: "x" }), { status: 200 }) as Response,
    );
    await getSkillFile("hello", "references/foo bar.md");
    expect(fetchMock.mock.calls[0][0]).toContain(
      "/skills/hello/files/references/foo%20bar.md",
    );
  });
});
