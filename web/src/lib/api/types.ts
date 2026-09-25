/**
 * Domain types, re-exported from the engine's own OpenAPI document.
 *
 * `generated.ts` is produced by `npm run generate:api` and is never edited by
 * hand. Everything the UI touches is aliased here so components import a plain
 * name (`TurnResponse`) rather than reaching into
 * `components["schemas"][...]` — and so a contract change surfaces as a type
 * error in one file instead of fifty.
 */

import type { components } from "./generated";

type Schemas = components["schemas"];

export type TurnResponse = Schemas["TurnResponse"];
export type OpenCallResponse = Schemas["OpenCallResponse"];
export type CallerOut = Schemas["CallerOut"];
export type PersonaOut = Schemas["PersonaOut"];
export type PersonaId = Schemas["PersonaId"];
export type CallOutcome = Schemas["CallOutcome"];
export type InputMode = Schemas["InputMode"];
export type StageTimingOut = Schemas["StageTimingOut"];

export type CustomerOut = Schemas["CustomerOut"];
export type PaymentStatus = Schemas["PaymentStatus"];
export type RouterStatus = Schemas["RouterStatus"];
export type LOSLightState = Schemas["LOSLightState"];

export type TelemetryResponse = Schemas["TelemetryResponse"];
export type TelemetrySummary = Schemas["TelemetrySummary"];
export type TelemetryEntryOut = Schemas["TelemetryEntryOut"];
export type StageAverage = Schemas["StageAverage"];
export type PersonaBreakdown = Schemas["PersonaBreakdown"];

export type SessionHistoryResponse = Schemas["SessionHistoryResponse"];
export type ConversationTurnOut = Schemas["ConversationTurnOut"];

export type ReadinessResponse = Schemas["ReadinessResponse"];
export type HealthResponse = Schemas["HealthResponse"];
export type ApiErrorBody = Schemas["ErrorResponse"];

/**
 * Canonical pipeline stages, in execution order.
 *
 * The chart ramp is assigned by this index, so a given stage is the same
 * colour in every chart on every page — never reshuffled because one stage
 * happened to be missing from the latest sample.
 */
export const PIPELINE_STAGES = [
  "input_processing",
  "crm_lookup",
  "llm_generation",
  "tts_synthesis",
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

export function stageChartColor(stage: string): string {
  const index = PIPELINE_STAGES.indexOf(stage as PipelineStage);
  return `var(--chart-${index >= 0 ? index + 1 : 1})`;
}
