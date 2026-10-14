export type View = 'ask' | 'library' | 'experts' | 'ingest' | 'team' | 'personal' | 'settings';
export type Paper = {
  id: string; title: string; authors: string; year: number; journal: string; topic: string;
  pages: number; chunks: number; filename: string; abstract: string; evidence: string;
  status?: 'pending'; uploadedBy?: string;
};
export type ExpertSelection = 'auto' | 'manual';
export type Answer = { id: string; question: string; mode: 'summary' | 'compare' | 'limits' | 'claims'; paperIds: string[]; authorId: string; visibility: 'team' | 'private'; deleted?: boolean; experts?: string[]; expertSelection?: ExpertSelection; contributions?: ExpertContribution[]; evidence?: AnswerEvidenceSnapshot };
export type Facet = 'platform' | 'application' | 'layer' | 'focus' | 'method';
export type ExpertFacets = Partial<Record<Facet,string[]>>;
// `scope` retains the stored prototype field name; it is the admin-authored overview, not an admission rule.
export type ExpertCategory = { id: string; name: string; scope: string; keywords: string; paperIds: string[]; enabled: boolean; facets?: ExpertFacets; reviewIds?: string[]; excludedIds?: string[]; includedIds?: string[]; updatedAt?: string };
export type ClaimMatch = { claimId:string; reason:string; measurementIds:string[] };
export type ExpertContribution = { id:string; name:string; scope:string; reason:string; paperIds:string[]; knowledgeTitles:string[]; asOf:string; claimMatches:ClaimMatch[]; measurementIds:string[] };

// A detached, in-memory response snapshot. Production revision storage is specified in DATABASE_PLAN.md.
export type AnswerEvidenceSnapshot = {
  capturedAt:string; selectionVersion:'fixture-rules-v1'; matches:ClaimMatch[];
  claims:Claim[]; measurements:Measurement[]; spans:EvidenceSpan[]; relations:ClaimRelation[];
};

// UI response models; not a database schema or extraction implementation.
export type EvidenceSpan = {
  id: string; paperId: string; page: number; section: string; locator: string;
  kind: 'paragraph' | 'table'; quote: string; documentVersion: string; extractionRunId: string;
};
export type Claim = {
  id: string; paperId: string; kind: 'result' | 'interpretation' | 'hypothesis' | 'limitation';
  attribution: 'authors' | 'cited-work'; summary: string; rawText: string; scope: string;
  certainty: string; validation: 'passed' | 'needs-review'; review: 'unreviewed' | 'reviewed';
  evidence: { spanId: string; role: 'statement' | 'support' | 'qualification' }[];
  measurementIds: string[]; hasDirectSupport: boolean;
};
export type Measurement = {
  id: string; paperId: string; label: string; rawValue: string; value: number;
  operator: '=' | '>='; unit: string; normalizedValue: number; normalizedUnit: string;
  sample: string; evidenceIds: string[];
  conditions: { name: string; rawValue: string | null; evidenceIds: string[] }[];
};
export type ClaimRelation = {
  id: string; from: string; to: string; kind: 'complements' | 'potential-conflict' | 'supports';
  comparisonStatus: 'different-conditions' | 'unknown' | 'comparable'; reason: string;
};
export type EvidenceDataset = { spans: EvidenceSpan[]; claims: Claim[]; measurements: Measurement[]; relations: ClaimRelation[] };
