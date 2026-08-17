/** Pure presentation transformations shared across reviewer GUI components. */

import type { ReviewKind, ReviewQueueItem, ReviewQueueSummary } from "./types";

/** Maximum number of letters displayed in a reviewer avatar. */
const REVIEWER_INITIALS_LENGTH = 2;

/** Number of characters in the date portion of an RFC 3339 timestamp. */
const RFC3339_DATE_LENGTH = 10;

/** Count at which an English display noun uses its singular form. */
const SINGULAR_COUNT = 1;

/** Honorific omitted from reviewer avatars because it adds no identifying letter. */
const LEADING_DOCTOR_HONORIFIC = /^Dr\s+/u;

/** Return a concise human label for a review queue kind. */
export function reviewKindLabel(kind: ReviewKind): string {
  return kind === "interaction_rule" ? "Interaction rule" : "Condition conflict";
}

/** Derive stable uppercase avatar initials from a reviewer's display name. */
export function reviewerInitials(fullName: string): string {
  return fullName
    .replace(LEADING_DOCTOR_HONORIFIC, "")
    .split(/\s+/u)
    .map((part: string): string => {
      const [initial = ""] = part;
      return initial;
    })
    .join("")
    .slice(0, REVIEWER_INITIALS_LENGTH)
    .toUpperCase();
}

/** Return the total number of unresolved records represented by a queue summary. */
export function unresolvedQueueCount(summary: ReviewQueueSummary): number {
  return summary.interactionRules + summary.conditionContradictions;
}

/** Keep an empty result set's page heading human-readable as "page 1 of 1". */
export function visiblePageCount(totalPages: number): number {
  return Math.max(totalPages, SINGULAR_COUNT);
}

/** Extract the calendar date from the service's RFC 3339 queue timestamp. */
export function queueReadDate(generatedAt: string): string {
  return generatedAt.slice(0, RFC3339_DATE_LENGTH);
}

/** Return the selected item when retained by a refreshed page, otherwise its first item. */
export function retainedQueueSelection(
  items: ReviewQueueItem[],
  selectedId: string,
): ReviewQueueItem | null {
  const [firstItem = null] = items;
  return items.find((item: ReviewQueueItem): boolean => item.id === selectedId) ?? firstItem;
}

/** Render the enrolled signing-key count with the correct English noun. */
export function keyCountLabel(keyCount: number): string {
  return `${keyCount} ${keyCount === SINGULAR_COUNT ? "key" : "keys"}`;
}
