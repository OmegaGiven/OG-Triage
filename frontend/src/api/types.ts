/**
 * Typed mirror of backend/api/schemas.py. Kept 1:1 with the Pydantic
 * response models on purpose -- if the backend contract changes, this file
 * should be the first thing touched.
 */

export const DENIAL_STATUSES = [
  "new",
  "processing",
  "classified",
  "appeal_drafted",
  "needs_review",
  "closed",
] as const;
export type DenialStatus = (typeof DENIAL_STATUSES)[number];

export const APPEAL_STATUSES = ["draft", "approved", "rejected", "sent"] as const;
export type AppealStatus = (typeof APPEAL_STATUSES)[number];

export const CLASSIFICATION_CATEGORIES = [
  "coding_error",
  "missing_information",
  "medical_necessity",
  "timely_filing",
  "eligibility",
  "duplicate_claim",
] as const;
export type ClassificationCategory = (typeof CLASSIFICATION_CATEGORIES)[number];

export interface DenialListItem {
  id: string;
  source_company: string;
  status: string;
  payer: string;
  claim_ref: string;
  received_at: string;
  has_classification: boolean;
  has_appeal: boolean;
}

export interface DenialListResponse {
  items: DenialListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ExtractionOut {
  id: number;
  extracted_fields: Record<string, unknown>;
  model_version: string;
  prompt_version: string;
  raw_model_output: string;
  created_at: string;
}

export interface ClassificationOut {
  id: number;
  category: string;
  confidence: number;
  model_version: string;
  created_at: string;
}

export interface AppealOut {
  id: number;
  draft_text: string;
  status: string;
  reviewer: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface CorrectionOut {
  id: number;
  field_corrected: string;
  old_value: string;
  new_value: string;
  corrected_by: string;
  corrected_at: string;
  notes: string | null;
}

export interface DenialDetail {
  id: string;
  source_company: string;
  status: string;
  payer: string;
  claim_ref: string;
  received_at: string;
  created_at: string;
  raw_text: string;
  extraction: ExtractionOut | null;
  classification: ClassificationOut | null;
  appeal: AppealOut | null;
  corrections: CorrectionOut[];
}

export interface ProcessResponse {
  denial_id: string;
  status: string;
}

export interface AppealStatusUpdateRequest {
  status: string;
  reviewer: string;
}

export interface CorrectionCreateRequest {
  field_corrected: string;
  old_value: string;
  new_value: string;
  corrected_by: string;
  notes?: string | null;
}

export interface EvalRunOut {
  id: number;
  run_at: string;
  accuracy_score: number | null;
  git_commit: string | null;
  details: Record<string, unknown> | null;
}

export interface ProfileOut {
  key: string;
  display_name: string;
}

export interface ExtractionFieldOut {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

export interface AppealGuidanceExcerpt {
  category: string;
  excerpt: string;
}

export interface ProfileDetailOut {
  key: string;
  display_name: string;
  extraction_fields: ExtractionFieldOut[];
  category_taxonomy: string[];
  appeal_guidance: AppealGuidanceExcerpt[];
}

export interface DemoResetOut {
  denial_id: string;
  source_company: string;
  claim_ref: string;
  status: string;
}

export interface UsageByStage {
  stage: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  call_count: number;
}

export interface UsageByDay {
  day: string;
  total_tokens: number;
  estimated_cost_usd: number;
  call_count: number;
}

export interface UsageResponse {
  source_company: string | null;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_estimated_cost_usd: number;
  total_calls: number;
  by_stage: UsageByStage[];
  by_day: UsageByDay[];
}

export interface ConfidenceBucket {
  label: string;
  min_confidence: number;
  max_confidence: number;
  count: number;
}

export interface ConfidenceDistributionResponse {
  source_company: string | null;
  total_classified: number;
  buckets: ConfidenceBucket[];
}
