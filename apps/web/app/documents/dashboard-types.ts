export interface UpcomingDeadline {
  fact_id: string;
  document_id: string;
  document_title: string;
  label: string;
  due_at: string;
  raw_date_text: string | null;
  days_remaining: number;
  is_primary: boolean;
  confidence_bucket: 'high' | 'medium' | 'low';
  normalization_status: string | null;
  source_page: number;
  task_id: string | null;
  task_status: string | null;
}

export interface OpenTask {
  id: string;
  title: string;
  status: string;
  priority: string;
  due_at: string | null;
  origin: string;
  document_id: string | null;
  document_title: string | null;
}

export interface RecentDocument {
  id: string;
  title: string;
  original_filename: string;
  status: string;
  language: string;
  document_type: string | null;
  page_count: number | null;
  created_at: string;
}

export interface Dashboard {
  documents: { total: number; processing: number; pages: number; cost_usd: number };
  facts: {
    total: number;
    deadlines: number;
    requirements: number;
    verified: number;
    high_confidence: number;
    citation_verification_rate: number | null;
  };
  tasks: { total: number; done: number; open: number; overdue: number };
  upcoming_deadlines: UpcomingDeadline[];
  open_tasks: OpenTask[];
  recent_documents: RecentDocument[];
}
