import { create } from 'zustand'

/** What the inspector is currently showing. */
export type Selection =
  | { kind: 'none' }
  | { kind: 'person'; id: string }
  | { kind: 'person-ord'; ord: number }   // clicked on the map, before the id is known
  | { kind: 'place'; id: string }

interface SelectionState {
  sel: Selection
  select: (s: Selection) => void
  clear: () => void
}

export const useSelection = create<SelectionState>((set) => ({
  sel: { kind: 'none' },
  select: (sel) => set({ sel }),
  clear: () => set({ sel: { kind: 'none' } }),
}))

// A handle for ui/scripts — clicking a specific building on a WebGL canvas from
// a test is guesswork; selecting one by id is not.
;(globalThis as any).__select = (s: Selection) => useSelection.getState().select(s)
