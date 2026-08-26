"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Textarea } from "@/components/ui/Textarea";
import { reviewStatusLabel, reviewStatusTone } from "@/lib/campaign-generation";
import type { CampaignApproval } from "@/types/campaign-generation";

/**
 * Sign-off for a generated campaign.
 *
 * Approval moves the package to READY_TO_PUBLISH and stops there. Publishing is
 * not implemented, so this panel states that plainly: a reviewer must not walk
 * away believing money is now being spent. The approve and reject controls are
 * hidden when the server says `can_approve` is false — and the server enforces
 * that regardless of what is rendered here.
 */
export function ApprovalPanel({
  approval,
  publishingNote,
  busy,
  onApprove,
  onReject,
}: {
  approval: CampaignApproval;
  publishingNote: string;
  busy: boolean;
  onApprove: (comment: string) => void;
  onReject: (reason: string) => void;
}) {
  const [comment, setComment] = useState("");
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);

  const status = approval.review_status.toUpperCase();
  const decided = status === "READY_TO_PUBLISH" || status === "APPROVED" || status === "REJECTED";
  const canDecide = approval.can_approve && !decided;

  return (
    <Card>
      <CardHeader
        title="Approval"
        subtitle={publishingNote}
        action={<Badge tone={reviewStatusTone(status)}>{reviewStatusLabel(status)}</Badge>}
      />

      {approval.approved_at ? (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <div className="font-medium">Approved</div>
          <div className="mt-1 text-xs">
            {new Date(approval.approved_at).toLocaleString()} · by {shortId(approval.approved_by)}
          </div>
          {approval.approval_comment ? (
            <p className="mt-2">{approval.approval_comment}</p>
          ) : null}
        </div>
      ) : null}

      {approval.rejected_at ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <div className="font-medium">Rejected</div>
          <div className="mt-1 text-xs">
            {new Date(approval.rejected_at).toLocaleString()} · by {shortId(approval.rejected_by)}
          </div>
          {approval.rejection_reason ? <p className="mt-2">{approval.rejection_reason}</p> : null}
        </div>
      ) : null}

      {canDecide ? (
        rejecting ? (
          <div className="space-y-3">
            <Textarea
              value={reason}
              rows={3}
              placeholder="Why is this campaign being rejected? Recorded on the campaign."
              onChange={(event) => setReason(event.target.value)}
            />
            <div className="flex gap-2">
              <Button
                variant="danger"
                disabled={busy || reason.trim().length === 0}
                onClick={() => onReject(reason.trim())}
              >
                Confirm rejection
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => setRejecting(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <Textarea
              value={comment}
              rows={2}
              placeholder="Optional approval note"
              onChange={(event) => setComment(event.target.value)}
            />
            <div className="flex gap-2">
              <Button disabled={busy} onClick={() => onApprove(comment.trim())}>
                {busy ? "Recording…" : "Approve campaign"}
              </Button>
              <Button variant="secondary" disabled={busy} onClick={() => setRejecting(true)}>
                Reject
              </Button>
            </div>
          </div>
        )
      ) : null}

      {!approval.can_approve && !decided ? (
        <p className="text-sm text-[var(--muted)]">
          Your role cannot approve campaigns. Ask an admin or owner to review this package.
        </p>
      ) : null}

      {status === "READY_TO_PUBLISH" ? (
        <p className="mt-4 rounded-xl bg-[var(--surface-2)] px-4 py-3 text-xs text-[var(--muted)]">
          Approved and ready to publish. Nothing has been sent to an ad platform and no budget has
          been committed — publishing is a later phase.
          {approval.external_id ? ` External campaign: ${approval.external_id}` : ""}
        </p>
      ) : null}
    </Card>
  );
}

function shortId(value: string | null): string {
  return value ? value.slice(0, 8) : "unknown";
}
