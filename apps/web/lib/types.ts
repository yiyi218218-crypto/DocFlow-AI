/**
 * API response types.
 *
 * Hand-written for now, mirroring apps/api/src/docflow/schemas/. The plan is to
 * generate these from the OpenAPI schema with a CI drift check
 * (docs/30-data-api.md §13.3) — until then, treat this file as the contract and
 * keep it in sync deliberately.
 */

export type DocumentStatus =
  | 'uploaded'
  | 'queued'
  | 'processing'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'quarantined';

export type StageStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

export type FactKind =
  | 'deadline'
  | 'requirement'
  | 'action_item'
  | 'entity'
  | 'contact'
  | 'link'
  | 'risk'
  | 'decision';

/** `unverified` is never rendered as confirmed; `page_only` navigates without a
 *  highlight rather than fabricating one. */
export type CitationStatus = 'verified' | 'partial' | 'unverified' | 'page_only';

export type ConfidenceBucket = 'high' | 'medium' | 'low';

export interface Stage {
  stage: string;
  status: StageStatus;
  attempt: number;
  duration_ms: number | null;
  error_code: string | null;
}

export interface Rect {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Fact {
  id: string;
  kind: FactKind;
  payload: Record<string, unknown>;
  source_page: number;
  source_quote: string;
  source_rects: Rect[];
  citation_status: CitationStatus;
  citation_match_score: number | null;
  confidence_bucket: ConfidenceBucket;
  confidence: number;
  due_at: string | null;
  normalization_status: string | null;
  is_primary: boolean;
  review_status: string;
}

export interface DocumentSummary {
  id: string;
  original_filename: string;
  title: string | null;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  language: string;
  document_type: string | null;
  status: DocumentStatus;
  error_code: string | null;
  error_detail: string | null;
  security_flags: string[];
  created_at: string;
  fact_count: number;
  deadline_count: number;
  next_deadline_at: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  summary: string | null;
  organization: string | null;
  reference_number: string | null;
  published_date: string | null;
  total_cost_usd: number;
  stages: Stage[];
}

export interface DocumentPage {
  page_number: number;
  width: number;
  height: number;
  rotation: number;
  extraction_method: string;
  ocr_confidence: number | null;
  citation_granularity: string;
  image_url: string | null;
}

export interface DocumentAnalysis {
  document: DocumentDetail;
  facts: Fact[];
  pages: DocumentPage[];
  counts_by_kind: Record<string, number>;
  confidence_summary: Record<string, number>;
  citation_verification_rate: number | null;
  low_confidence_hidden: number;
}

export interface Paginated<T> {
  data: T[];
  next_cursor: string | null;
  has_more: boolean;
}

export const FACT_LABELS: Record<FactKind, string> = {
  deadline: 'Deadlines',
  requirement: 'Required',
  action_item: 'Actions',
  entity: 'Entities',
  contact: 'Contacts',
  link: 'Links',
  risk: 'Risks',
  decision: 'Decisions',
};

export const STAGE_LABELS: Record<string, string> = {
  validating: 'Checking the file',
  normalizing: 'Converting pages',
  parsing: 'Reading text',
  ocr: 'Recognising scanned text',
  segmenting: 'Detecting language',
  classifying: 'Classifying',
  extracting: 'Extracting facts',
  aligning: 'Verifying citations',
  chunking: 'Indexing',
  embedding: 'Building search index',
  indexing: 'Finishing up',
};

/** Terminal states stop the poller. */
export function isTerminal(status: DocumentStatus): boolean {
  return ['completed', 'partial', 'failed', 'quarantined'].includes(status);
}
