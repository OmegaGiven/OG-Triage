import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { StatusPill } from "../components/StatusPill";
import { ConfidenceBadge } from "../components/ConfidenceBadge";
import { KeyValueGrid } from "../components/KeyValue";
import { DetailSkeleton, ErrorState, Spinner } from "../components/States";
import { Modal } from "../components/Modal";
import { CorrectionForm } from "../components/CorrectionForm";

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function DetailView() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCorrectionForm, setShowCorrectionForm] = useState(false);
  const [reviewerName, setReviewerName] = useState("");

  const denialQuery = useQuery({
    queryKey: ["denial", id],
    queryFn: () => api.getDenial(id!),
    enabled: !!id,
  });

  const processMutation = useMutation({
    mutationFn: () => api.processDenial(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["denial", id] });
      queryClient.invalidateQueries({ queryKey: ["denials"] });
    },
  });

  const appealStatusMutation = useMutation({
    mutationFn: (status: "approved" | "rejected") =>
      api.updateAppealStatus(id!, { status, reviewer: reviewerName || "reviewer@example.com" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["denial", id] });
      queryClient.invalidateQueries({ queryKey: ["denials"] });
    },
  });

  if (denialQuery.isLoading) {
    return (
      <div>
        <BackLink />
        <DetailSkeleton />
      </div>
    );
  }

  if (denialQuery.isError) {
    return (
      <div>
        <BackLink />
        <ErrorState
          message={
            denialQuery.error instanceof ApiError
              ? denialQuery.error.message
              : "Failed to load this denial."
          }
          onRetry={() => denialQuery.refetch()}
        />
      </div>
    );
  }

  const denial = denialQuery.data!;

  return (
    <div>
      <BackLink />

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-ink-900">{denial.claim_ref}</h1>
            <StatusPill status={denial.status} />
          </div>
          <p className="mt-1 text-sm text-ink-500">
            {denial.payer} · Received {formatDateTime(denial.received_at)} ·{" "}
            {denial.source_company.replace(/_/g, " ")}
          </p>
        </div>

        {denial.status === "new" && (
          <button
            className="btn-primary"
            disabled={processMutation.isPending}
            onClick={() => processMutation.mutate()}
          >
            {processMutation.isPending ? (
              <>
                <Spinner className="h-4 w-4" />
                Processing (this can take 10-30s)…
              </>
            ) : (
              "Process with AI"
            )}
          </button>
        )}
      </div>

      {processMutation.isPending && (
        <div className="mb-6 flex items-center gap-3 rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-800">
          <Spinner className="h-4 w-4 text-brand-600" />
          Running extraction, classification, and appeal drafting against the real Anthropic
          API. This can take up to 30 seconds — the page will update automatically.
        </div>
      )}

      {processMutation.isError && (
        <div className="mb-6 rounded-lg border border-status-rejected/30 bg-status-rejected-bg px-4 py-3 text-sm text-status-rejected-fg">
          Processing failed: {(processMutation.error as Error).message}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: evidence panel */}
        <div className="card p-6">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-500">
            Original Denial Letter
          </h2>
          <pre className="max-h-[600px] overflow-y-auto whitespace-pre-wrap rounded-md bg-ink-50 p-4 font-mono text-[13px] leading-relaxed text-ink-700 border border-ink-100">
            {denial.raw_text}
          </pre>
        </div>

        {/* Right: extraction + classification + appeal */}
        <div className="space-y-6">
          <div className="card p-6">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-500">
              Extracted Fields
            </h2>
            {denial.extraction ? (
              <KeyValueGrid fields={denial.extraction.extracted_fields} />
            ) : (
              <p className="text-sm text-ink-400">Not yet processed.</p>
            )}
          </div>

          <div className="card p-6">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-500">
              Classification
            </h2>
            {denial.classification ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <span className="rounded-md bg-brand-50 px-2.5 py-1 text-sm font-semibold text-brand-700">
                    {denial.classification.category.replace(/_/g, " ")}
                  </span>
                </div>
                <ConfidenceBadge confidence={denial.classification.confidence} />
              </div>
            ) : (
              <p className="text-sm text-ink-400">Not yet classified.</p>
            )}
          </div>
        </div>
      </div>

      {/* Appeal draft — full width, paired below for side-by-side comparison */}
      <div className="mt-6 card p-6">
        <div className="mb-3 flex items-center justify-between flex-wrap gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-500">
            Drafted Appeal Letter
          </h2>
          {denial.appeal && <StatusPill status={denial.appeal.status} />}
        </div>

        {denial.appeal ? (
          <>
            <pre className="max-h-[500px] overflow-y-auto whitespace-pre-wrap rounded-md bg-ink-50 p-4 font-mono text-[13px] leading-relaxed text-ink-700 border border-ink-100">
              {denial.appeal.draft_text}
            </pre>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <input
                className="input max-w-xs"
                placeholder="Your name / email (reviewer)"
                value={reviewerName}
                onChange={(e) => setReviewerName(e.target.value)}
              />
              <button
                className="btn-success"
                disabled={appealStatusMutation.isPending || !reviewerName}
                onClick={() => appealStatusMutation.mutate("approved")}
              >
                Approve Appeal
              </button>
              <button
                className="btn-danger"
                disabled={appealStatusMutation.isPending || !reviewerName}
                onClick={() => appealStatusMutation.mutate("rejected")}
              >
                Reject Appeal
              </button>
              <button className="btn-secondary" onClick={() => setShowCorrectionForm(true)}>
                Log a Correction
              </button>
              {appealStatusMutation.isPending && <Spinner className="h-4 w-4 text-ink-400" />}
            </div>
            {denial.appeal.reviewer && (
              <p className="mt-2 text-xs text-ink-400">
                Last reviewed by {denial.appeal.reviewer} on {formatDateTime(denial.appeal.reviewed_at)}
              </p>
            )}
            {appealStatusMutation.isError && (
              <p className="mt-2 text-sm text-status-rejected-fg">
                {(appealStatusMutation.error as Error).message}
              </p>
            )}
          </>
        ) : (
          <div className="flex items-center justify-between">
            <p className="text-sm text-ink-400">No appeal drafted yet.</p>
            {denial.classification && (
              <button className="btn-secondary" onClick={() => setShowCorrectionForm(true)}>
                Log a Correction
              </button>
            )}
          </div>
        )}
      </div>

      {/* Corrections / audit trail */}
      <div className="mt-6 card p-6">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-500">
          Correction History
          <span className="ml-2 rounded-full bg-ink-100 px-2 py-0.5 text-xs font-semibold text-ink-500">
            {denial.corrections.length}
          </span>
        </h2>
        {denial.corrections.length === 0 ? (
          <p className="text-sm text-ink-400">No corrections logged for this denial.</p>
        ) : (
          <ul className="divide-y divide-ink-100">
            {denial.corrections.map((c) => (
              <li key={c.id} className="py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium text-ink-800">{c.field_corrected}</span>
                  <span className="text-xs text-ink-400">
                    {c.corrected_by} · {formatDateTime(c.corrected_at)}
                  </span>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-sm">
                  <span className="rounded bg-status-rejected-bg px-2 py-0.5 text-status-rejected-fg line-through decoration-1">
                    {c.old_value || "—"}
                  </span>
                  <svg className="h-3.5 w-3.5 text-ink-300" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path
                      fillRule="evenodd"
                      d="M10.293 3.293a1 1 0 011.414 0l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414-1.414L12.586 9H3a1 1 0 110-2h9.586l-2.293-2.293a1 1 0 010-1.414z"
                      clipRule="evenodd"
                    />
                  </svg>
                  <span className="rounded bg-status-approved-bg px-2 py-0.5 text-status-approved-fg">
                    {c.new_value || "—"}
                  </span>
                </div>
                {c.notes && <p className="mt-1.5 text-sm text-ink-500">{c.notes}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {showCorrectionForm && (
        <Modal title="Log a correction" onClose={() => setShowCorrectionForm(false)}>
          <CorrectionForm
            denialId={denial.id}
            currentCategory={denial.classification?.category}
            currentAppealText={denial.appeal?.draft_text}
            onDone={() => setShowCorrectionForm(false)}
          />
        </Modal>
      )}
    </div>
  );

  function BackLink() {
    return (
      <button
        onClick={() => navigate("/denials")}
        className="mb-4 inline-flex items-center gap-1 text-sm font-medium text-ink-500 hover:text-brand-600"
      >
        <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path
            fillRule="evenodd"
            d="M12.707 5.293a1 1 0 010 1.414L9.414 10l3.293 3.293a1 1 0 01-1.414 1.414l-4-4a1 1 0 010-1.414l4-4a1 1 0 011.414 0z"
            clipRule="evenodd"
          />
        </svg>
        Back to queue
      </button>
    );
  }
}
