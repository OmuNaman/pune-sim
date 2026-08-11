import { create } from 'zustand'

/** What the inspector is currently showing. */
export type Selection =
  | { kind: 'none' }
  | { kind: 'person'; id: string }
  | { kind: 'person-ord'; ord: number }   // clicked on the map, before the id is known
  | { kind: 'place'; id: string }

interface SelectionState {
  sel: Selection
  /** The person pinned for comparison, if any. */
  pinned: string | null
  select: (s: Selection) => void
  clear: () => void
  pin: (id: string | null) => void
}

export const useSelection = create<SelectionState>((set) => ({
  sel: { kind: 'none' },
  pinned: null,
  select: (sel) => set({ sel }),
  clear: () => set({ sel: { kind: 'none' } }),
  pin: (pinned) => set({ pinned }),
}))

// A handle for ui/scripts — clicking a specific building on a WebGL canvas from
// a test is guesswork; selecting one by id is not.
;(globalThis as any).__select = (s: Selection) => useSelection.getState().select(s)
