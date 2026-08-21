'use client';

/**
 * The PDF viewer and highlight overlay.
 *
 * The coordinate contract is the whole point of this file. The backend returns
 * rectangles already normalised to [0,1] against the page's DISPLAY box, with
 * page rotation applied server-side. So the browser does exactly one
 * multiplication:
 *
 *     left = rect.x0 * viewport.width
 *
 * No rotation maths, no DPI conversion, no fudge factors. If a highlight is
 * misaligned the bug is upstream in the text map — compensating for it here
 * would look right on one document and wrong on the next.
 *
 * Pages render on demand via IntersectionObserver, but every page container is
 * sized from the API's page dimensions *before* anything renders, so the scroll
 * height is correct from the start and there is no layout shift while scrolling.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { fetchBlobUrl } from '@/lib/api';
import { useViewerStore } from '@/lib/stores/viewer';
import type { DocumentPage, Rect } from '@/lib/types';
import { cn } from '@/lib/utils';

interface PdfViewerProps {
  fileUrl: string;
  pages: DocumentPage[];
}

const BASE_WIDTH = 820; // CSS px at zoom 1

export function PdfViewer({ fileUrl, pages }: PdfViewerProps) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const zoom = useViewerStore((s) => s.zoom);
  const activeHighlight = useViewerStore((s) => s.activeHighlight);
  const scrollNonce = useViewerStore((s) => s.scrollNonce);
  const setCurrentPage = useViewerStore((s) => s.setCurrentPage);

  // pdfjs is ~340 kB and must never enter the shared bundle, so it is imported
  // dynamically on this route only.
  useEffect(() => {
    let cancelled = false;
    let doc: PDFDocumentProxy | null = null;

    let objectUrl: string | null = null;

    (async () => {
      try {
        // Authenticated fetch first: pdf.js cannot attach our bearer token.
        objectUrl = await fetchBlobUrl(fileUrl);
        if (cancelled) return;

        const pdfjs = await import('pdfjs-dist');
        // The worker is copied into /public by scripts/copy-pdf-worker.mjs.
        // Next does not bundle it, and a `?url` import does not resolve during
        // `next build` — this is the first bug every pdf.js integration hits.
        pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';

        const task = pdfjs.getDocument({
          url: objectUrl,
          // PDFs can carry JavaScript and XFA. We render untrusted documents,
          // so both stay off (docs/50-security-ops.md §16.5).
          isEvalSupported: false,
          enableXfa: false,
        });
        doc = await task.promise;
        if (!cancelled) setPdf(doc);
      } catch (error) {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : 'Could not open this PDF.');
        }
      }
    })();

    return () => {
      cancelled = true;
      void doc?.destroy();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileUrl]);

  // Scroll to the page carrying the active highlight.
  useEffect(() => {
    if (!activeHighlight || !containerRef.current) return;
    const target = containerRef.current.querySelector<HTMLElement>(
      `[data-page="${activeHighlight.page}"]`,
    );
    if (!target) return;

    const distance = Math.abs(activeHighlight.page - useViewerStore.getState().currentPage);
    target.scrollIntoView({
      // Smooth-scrolling 40 pages takes seconds and reads as broken.
      behavior: distance > 3 ? 'auto' : 'smooth',
      block: 'center',
    });
  }, [activeHighlight, scrollNonce]);

  const onPageVisible = useCallback(
    (pageNumber: number) => setCurrentPage(pageNumber),
    [setCurrentPage],
  );

  if (loadError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <p className="text-sm font-medium">This PDF could not be displayed</p>
        <p className="max-w-sm text-xs text-muted">{loadError}</p>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="h-full overflow-y-auto bg-surface-2 px-6 py-6">
      <div className="mx-auto flex flex-col items-center gap-5">
        {pages.map((page) => (
          <PageCanvas
            key={page.page_number}
            pdf={pdf}
            page={page}
            zoom={zoom}
            highlight={
              activeHighlight?.page === page.page_number ? activeHighlight.rects : null
            }
            highlightKey={scrollNonce}
            onVisible={onPageVisible}
          />
        ))}
      </div>
    </div>
  );
}

interface PageCanvasProps {
  pdf: PDFDocumentProxy | null;
  page: DocumentPage;
  zoom: number;
  highlight: Rect[] | null;
  highlightKey: number;
  onVisible: (pageNumber: number) => void;
}

function PageCanvas({ pdf, page, zoom, highlight, highlightKey, onVisible }: PageCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [rendered, setRendered] = useState(false);
  const [inView, setInView] = useState(false);

  // Size the container from the API's page dimensions before anything renders,
  // so the scrollbar is correct immediately and nothing jumps.
  const { width, height } = useMemo(() => {
    const aspect = page.height > 0 ? page.height / page.width : 1.414;
    const w = BASE_WIDTH * zoom;
    return { width: w, height: w * aspect };
  }, [page.width, page.height, zoom]);

  useEffect(() => {
    const node = wrapperRef.current;
    if (!node) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry) return;
        if (entry.isIntersecting) {
          setInView(true);
          if (entry.intersectionRatio > 0.4) onVisible(page.page_number);
        }
      },
      // Render a screenful ahead so scrolling never waits on a canvas.
      { rootMargin: '1200px 0px', threshold: [0, 0.4] },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [page.page_number, onVisible]);

  useEffect(() => {
    if (!pdf || !inView || !canvasRef.current) return;
    let cancelled = false;

    (async () => {
      const pdfPage = await pdf.getPage(page.page_number);
      const unscaled = pdfPage.getViewport({ scale: 1 });
      const scale = (BASE_WIDTH * zoom) / unscaled.width;
      const viewport = pdfPage.getViewport({ scale });

      const canvas = canvasRef.current;
      if (!canvas || cancelled) return;

      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;

      const context = canvas.getContext('2d');
      if (!context) return;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);

      await pdfPage.render({ canvasContext: context, viewport }).promise;
      if (!cancelled) setRendered(true);
    })().catch(() => {
      /* The cached page image below remains as the fallback. Coordinates are
         identical either way, which is the payoff for normalising rects. */
    });

    return () => {
      cancelled = true;
    };
  }, [pdf, inView, page.page_number, zoom]);

  return (
    <div
      ref={wrapperRef}
      data-page={page.page_number}
      className="relative shrink-0 scroll-mt-6 border border-border bg-white"
      style={{ width, height }}
    >
      {/* Rendered image first: it appears instantly and is replaced by the
          canvas. Also the fallback if canvas rendering fails. */}
      {page.image_url && !rendered && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={page.image_url}
          alt={`Page ${page.page_number}`}
          className="absolute inset-0 h-full w-full object-contain"
          loading="lazy"
        />
      )}

      <canvas ref={canvasRef} className="absolute inset-0" />

      {highlight && (
        <div className="pointer-events-none absolute inset-0">
          {highlight.map((rect, index) => (
            <div
              key={`${highlightKey}-${index}`}
              data-testid="citation-highlight"
              className={cn('citation-rect', 'citation-rect--active')}
              style={{
                // The entire coordinate contract, in four lines.
                left: `${rect.x0 * 100}%`,
                top: `${rect.y0 * 100}%`,
                width: `${(rect.x1 - rect.x0) * 100}%`,
                height: `${(rect.y1 - rect.y0) * 100}%`,
              }}
            />
          ))}
        </div>
      )}

      <span className="absolute -bottom-px right-2 translate-y-full pt-1 font-mono text-[10px] text-faint">
        {page.page_number}
      </span>
    </div>
  );
}
