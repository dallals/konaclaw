import { describe, it, expect, beforeEach } from "vitest";
import { useApprovals } from "../../src/store/approvals";

beforeEach(() => useApprovals.setState({ pending: [] }));

describe("approvals store", () => {
  it("addRequest appends", () => {
    useApprovals.getState().addRequest({ request_id: "r1", agent: "kc", tool: "x", arguments: {} });
    expect(useApprovals.getState().pending).toHaveLength(1);
  });

  it("resolve removes by id", () => {
    const s = useApprovals.getState();
    s.addRequest({ request_id: "r1", agent: "kc", tool: "x", arguments: {} });
    s.addRequest({ request_id: "r2", agent: "kc", tool: "y", arguments: {} });
    s.resolveLocal("r1");
    expect(useApprovals.getState().pending.map((p) => p.request_id)).toEqual(["r2"]);
  });
});
