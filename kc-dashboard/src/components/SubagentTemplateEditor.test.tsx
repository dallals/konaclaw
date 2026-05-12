import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SubagentTemplateEditor } from "./SubagentTemplateEditor";

vi.mock("../api/subagents", () => ({
  createSubagentTemplate: vi.fn(),
  updateSubagentTemplate: vi.fn(),
}));

const DEFAULT_YAML_SNIPPET = "name: my-subagent";

describe("SubagentTemplateEditor", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("renders with default YAML in create mode", () => {
    render(
      <SubagentTemplateEditor
        mode="create"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    const textarea = screen.getByRole("textbox", { name: /template yaml/i });
    expect(textarea).toBeInTheDocument();
    expect((textarea as HTMLTextAreaElement).value).toContain(DEFAULT_YAML_SNIPPET);
  });

  it("renders with initialYaml when provided in edit mode", () => {
    const yaml = `name: my-agent\ndescription: test\n`;
    render(
      <SubagentTemplateEditor
        mode="edit"
        initialName="my-agent"
        initialYaml={yaml}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    const textarea = screen.getByRole("textbox", { name: /template yaml/i });
    expect((textarea as HTMLTextAreaElement).value).toBe(yaml);
  });

  it("shows edit label in header when in edit mode", () => {
    render(
      <SubagentTemplateEditor
        mode="edit"
        initialName="my-agent"
        initialYaml="name: my-agent\n"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByText(/Edit · my-agent/)).toBeInTheDocument();
  });

  it("shows New Template label in create mode", () => {
    render(
      <SubagentTemplateEditor
        mode="create"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByText("New Template")).toBeInTheDocument();
  });

  it("save in create mode calls createSubagentTemplate and fires onSaved", async () => {
    const { createSubagentTemplate } = await import("../api/subagents");
    const mockedCreate = vi.mocked(createSubagentTemplate);
    mockedCreate.mockResolvedValue({ name: "my-subagent" });

    const onSaved = vi.fn();
    render(
      <SubagentTemplateEditor
        mode="create"
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(mockedCreate).toHaveBeenCalledOnce());
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  });

  it("save in edit mode calls updateSubagentTemplate with name and yaml", async () => {
    const { updateSubagentTemplate } = await import("../api/subagents");
    const mockedUpdate = vi.mocked(updateSubagentTemplate);
    mockedUpdate.mockResolvedValue({ name: "my-agent" });

    const yaml = `name: my-agent\n`;
    const onSaved = vi.fn();
    render(
      <SubagentTemplateEditor
        mode="edit"
        initialName="my-agent"
        initialYaml={yaml}
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(mockedUpdate).toHaveBeenCalledWith("my-agent", yaml));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  });

  it("an error from the API surfaces in role=alert", async () => {
    const { createSubagentTemplate } = await import("../api/subagents");
    const mockedCreate = vi.mocked(createSubagentTemplate);
    mockedCreate.mockRejectedValue(new Error("422: invalid yaml"));

    render(
      <SubagentTemplateEditor
        mode="create"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("422: invalid yaml"),
    );
  });

  it("cancel fires onClose", () => {
    const onClose = vi.fn();
    render(
      <SubagentTemplateEditor
        mode="create"
        onClose={onClose}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking the backdrop fires onClose", () => {
    const onClose = vi.fn();
    render(
      <SubagentTemplateEditor
        mode="create"
        onClose={onClose}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
