import { create } from "zustand";
import type { ApprovalRequest } from "../ws/types";

type State = {
  pending: ApprovalRequest[];
  addRequest: (r: ApprovalRequest) => void;
  resolveLocal: (request_id: string) => void;
};

export const useApprovals = create<State>((set) => ({
  pending: [],
  addRequest: (r) => set((s) => ({ pending: [...s.pending, r] })),
  resolveLocal: (id) => set((s) => ({ pending: s.pending.filter((p) => p.request_id !== id) })),
}));
