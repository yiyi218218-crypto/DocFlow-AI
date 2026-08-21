'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRef, useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import type { DocumentSummary, Paginated } from '@/lib/types';
import { cn, deadlineUrgency, formatBytes, formatDeadline, relativeTime } from '@/lib/utils';

export default function DocumentsPage() {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['documents', 'list'],
    queryFn: () => apiFetch<Paginated<DocumentSummary>>('/api/v1/documents?limit=50'),
    // Poll while anything is still processing, then stop.
    refetchInterval: (query) => {
      const rows = query.state.data?.data ?? [];
      return rows.some((d) => d.status === 'queued' || d.status === 'processing') ? 2000 : false;
    },
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      return apiFetch<{ id: string }>('/api/v1/documents', { method: 'POST', body: form });
    },
    onSuccess: () => {
      setUploadError(null);
      void queryClient.invalidateQueries({ queryKey: ['documents'] });
    },
    onError: (error) => {
      setUploadError(
        error instanceof ApiError ? error.problem.detail : 'Upload failed. Try again.',
      );
    },
  });

  const documents = data?.data ?? [];

  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Documents</h1>
          <p className="mt-0.5 text-sm text-muted">
            Upload a circular or notice to extract its deadlines and requirements.
          </p>
        </div>

        <input
          ref={fileInput}
          type="file"
          accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file);
            event.target.value = '';
          }}
        />
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
          className="h-8 rounded-[--radius] bg-accent px-3 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {upload.isPending ? 'Uploading…' : 'Upload document'}
        </button>
      </div>

      {uploadError && (
        <p role="alert" className="mt-3 rounded-[--radius] border border-danger/30 bg-surface p-2.5 text-xs text-danger">
          {uploadError}
        </p>
      )}

      <div className="mt-6">
        {isLoading ? (
          <SkeletonList />
        ) : documents.length === 0 ? (
          <EmptyState onUpload={() => fileInput.current?.click()} />
        ) : (
          <ul className="divide-y divide-border overflow-hidden rounded-[--radius] border border-border bg-surface">
            {documents.map((document) => (
              <DocumentRow key={document.id} document={document} />
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}

function DocumentRow({ document }: { document: DocumentSummary }) {
  const processing = document.status === 'queued' || document.status === 'processing';
  const failed = document.status === 'failed' || document.status === 'quarantined';
  const urgency = deadlineUrgency(document.next_deadline_at);

  return (
    <li>
      <Link
        href={`/documents/${document.id}`}
        data-testid="document-row"
        data-filename={document.original_filename}
        className="flex items-center gap-4 px-4 py-3 transition-colors hover:bg-surface-2"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-[13px] font-medium">
              {document.title ?? document.original_filename}
            </p>
            {document.security_flags.includes('injection_suspected') && (
              <span
                className="shrink-0 rounded border border-medium/40 px-1 font-mono text-[10px] text-medium"
                title="This document contains text that attempts to manipulate AI systems"
              >
                flagged
              </span>
            )}
          </div>

          <p className="mt-0.5 flex items-center gap-2 font-mono text-[11px] text-faint">
            <span>{formatBytes(document.size_bytes)}</span>
            {document.page_count ? <span>· {document.page_count}p</span> : null}
            <span>· {relativeTime(document.created_at)}</span>
          </p>
        </div>

        {processing ? (
          <span className="flex items-center gap-1.5 font-mono text-[11px] text-muted">
            <span className="size-1.5 animate-pulse rounded-full bg-accent" />
            processing
          </span>
        ) : failed ? (
          <span className="font-mono text-[11px] text-danger">{document.error_code}</span>
        ) : (
          <div className="flex items-center gap-4">
            {document.next_deadline_at && (
              <span
                className={cn(
                  'font-mono text-[11px]',
                  urgency === 'overdue' && 'text-danger',
                  urgency === 'soon' && 'text-medium',
                  urgency === 'later' && 'text-muted',
                )}
              >
                {formatDeadline(document.next_deadline_at)}
              </span>
            )}
            <span className="font-mono text-[11px] text-faint">
              {document.fact_count} facts
            </span>
          </div>
        )}
      </Link>
    </li>
  );
}

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="flex flex-col items-center rounded-[--radius] border border-dashed border-border-strong bg-surface px-6 py-16 text-center">
      <p className="text-sm font-medium">No documents yet</p>
      <p className="mt-1 max-w-sm text-xs text-muted">
        Upload a university circular, scholarship notice or any PDF. DocFlow finds
        the deadlines and required documents, and shows you exactly where each one
        came from.
      </p>
      <button
        type="button"
        onClick={onUpload}
        className="mt-4 h-8 rounded-[--radius] border border-border-strong px-3 text-xs font-medium hover:bg-surface-2"
      >
        Upload your first document
      </button>
    </div>
  );
}

function SkeletonList() {
  return (
    <ul className="divide-y divide-border overflow-hidden rounded-[--radius] border border-border bg-surface">
      {[0, 1, 2].map((row) => (
        <li key={row} className="flex items-center gap-4 px-4 py-3.5">
          <div className="flex-1">
            <div className="h-3 w-52 rounded bg-surface-2" />
            <div className="mt-2 h-2.5 w-28 rounded bg-surface-2" />
          </div>
          <div className="h-2.5 w-20 rounded bg-surface-2" />
        </li>
      ))}
    </ul>
  );
}
