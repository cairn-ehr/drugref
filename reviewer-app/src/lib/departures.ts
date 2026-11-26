/** Departed graded pairs (#283): native adapter plus an isolated browser preview. */

import { invoke } from "@tauri-apps/api/core";
import type {
  AcknowledgeDepartureRequest,
  DepartureAcknowledgement,
  GradedPairDeparture,
} from "./types";

/** Whether calls may cross the protected native trust boundary. */
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Browser-only departures so the layout can be inspected without a service. */
let previewDepartures: GradedPairDeparture[] = [
  {
    ruleGrain: "class_rule",
    rulingId: 41,
    subjectKey: "00000000-0000-4000-8000-000000000041",
    subjectName: "Monoamine Oxidase Inhibitors [MoA]",
    objectClassUuid: "00000000-0000-4000-8000-000000000042",
    objectClassName: "Serotonin Reuptake Inhibitors [MoA]",
    relationship: "CI_MoA",
    severity: "contraindicated",
    reviewedBy: "Dr Maya Chen",
    reviewedAgainst: "MED-RT 2026.07.06",
    backfilled: false,
    firstSeenAt: "2026-08-02T09:14:00Z",
    pairCount: 3,
    leavers: [
      { moietyUuid: "00000000-0000-4000-8000-000000000043", displayName: "vortioxetine", closes: 3 },
    ],
  },
];

/** Load every open departure: the same list `drugref audit departures` prints. */
export async function loadGradedPairDepartures(): Promise<GradedPairDeparture[]> {
  if (isTauri) return invoke<GradedPairDeparture[]>("load_graded_pair_departures");
  return previewDepartures.map((departure) => ({ ...departure, leavers: [...departure.leavers] }));
}

/** Acknowledge one drug's departure; the service records who and against what. */
export async function acknowledgeGradedPairDeparture(
  input: AcknowledgeDepartureRequest,
): Promise<DepartureAcknowledgement> {
  if (isTauri) return invoke<DepartureAcknowledgement>("acknowledge_graded_pair_departure", { input });
  const departure = previewDepartures.find((item) => item.rulingId === input.rulingId);
  const leaver = departure?.leavers.find((item) => item.moietyUuid === input.moietyUuid);
  if (!departure || !leaver) throw new Error("nothing of this drug's departure is still open");
  previewDepartures = previewDepartures.filter((item) => item !== departure);
  return { closedPairs: leaver.closes, reviewedAgainst: "MED-RT 2026.07.06 (preview)" };
}
