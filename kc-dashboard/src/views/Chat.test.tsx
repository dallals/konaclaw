import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AssistantBubble } from "./Chat";

describe("AssistantBubble badge", () => {
  it("renders 'from reminder #42' when scheduled_job_id is set", () => {
    render(
      <MemoryRouter>
        <AssistantBubble content="hi" scheduled_job_id={42} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/from reminder #42/i)).toBeInTheDocument();
  });

  it("renders no footer when scheduled_job_id is null/undefined", () => {
    render(
      <MemoryRouter>
        <AssistantBubble content="hi" scheduled_job_id={null} />
      </MemoryRouter>,
    );
    expect(screen.queryByText(/from reminder/i)).not.toBeInTheDocument();
  });
});
