/**
 * Viewer state — one of exactly two Zustand stores.
 *
 * Justified: it updates at scroll frequency and is read by three sibling
 * component trees (the canvas, the highlight layer, the fact list). Context here
 * causes a re-render storm across the whole viewer on every scroll tick.
 *
 * Everything else in the app lives in TanStack Query, the URL, or local state.
 */

import { create } from 'zustand';
import type { Rect } from '@/lib/types';

interface ActiveHighlight {
  factId: string;
  page: number;
  rects: Rect[];
}

interface ViewerState {
  currentPage: number;
  zoom: number;
  activeHighlight: ActiveHighlight | null;
  /** Bumped on every navigation so the pulse replays even for the same fact. */
  scrollNonce: number;

  setCurrentPage: (page: number) => void;
  setZoom: (zoom: number) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  focusFact: (highlight: ActiveHighlight) => void;
  clearHighlight: () => void;
}

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

export const useViewerStore = create<ViewerState>((set, get) => ({
  currentPage: 1,
  zoom: 1,
  activeHighlight: null,
  scrollNonce: 0,

  setCurrentPage: (page) => set({ currentPage: page }),
  setZoom: (zoom) => set({ zoom }),

  zoomIn: () => {
    const next = ZOOM_STEPS.find((z) => z > get().zoom);
    if (next) set({ zoom: next });
  },
  zoomOut: () => {
    const below = ZOOM_STEPS.filter((z) => z < get().zoom);
    const next = below[below.length - 1];
    if (next) set({ zoom: next });
  },

  focusFact: (highlight) =>
    set((state) => ({
      activeHighlight: highlight,
      currentPage: highlight.page,
      scrollNonce: state.scrollNonce + 1,
    })),

  clearHighlight: () => set({ activeHighlight: null }),
}));
