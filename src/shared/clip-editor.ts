/** Persisted source-time edits. Media paths and review results are main-process owned. */
export type Crop = [number, number, number, number]
export type CropCorner = 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'
export type EditorRange = [number, number]
export interface CameraScan {
  start_ms: number; end_ms: number
  /** Presentation times from every decoded source frame, never a guessed FPS. */
  frames: number[]
  markers: { at_ms: number; score: number }[]
}
export interface EditorScene {
  at_ms: number; layout: 'fill' | 'split' | 'fit' | 'debate'; crops: Crop[]
  /** Incoming eased movement, in source milliseconds. Absent/zero is a cut. */
  transition_ms?: number
  /** Two panels in reverse order: crops stay top to bottom, panel heights swap (Debate's speaker on top). */
  swapped?: boolean
  /** Per crop, shown mirrored left to right. */
  mirror?: boolean[]
}
export interface EditorQuestion {
  id: string; prompt: string; yes: string; no: string; probability: number | null; threshold: number; status: string
}
export interface EditorReview {
  signature: string; reviewed_at: string; decision: string; questions: EditorQuestion[]
  cuts: { interval: EditorRange; questions: EditorQuestion[] }[]
}
/** Title header faces; ids match rendering_service.TITLE_FONTS, families match globals.css. */
export const TITLE_FONTS = [
  { id: 'montserrat', label: 'Montserrat', family: 'Montserrat', weight: 900, italic: false },
  { id: 'poppins', label: 'Poppins', family: 'Poppins', weight: 900, italic: false },
  { id: 'anton', label: 'Anton', family: 'Anton', weight: 400, italic: false },
  { id: 'archivo', label: 'Archivo Black', family: 'Archivo Black', weight: 400, italic: false },
  { id: 'serif', label: 'Instrument Serif', family: 'Instrument Serif', weight: 400, italic: true }
] as const
export type TitleFont = typeof TITLE_FONTS[number]['id']
export interface CandidateEdit {
  id: string; title: string; ranges: EditorRange[]; scenes: EditorScene[]
  captions: boolean; caption_preset: string; video_speed: number
  status: 'refining' | 'ready' | 'baked' | 'discarded'
  caption_edits: { segment: number; text: string }[]
  /** Source-time intervals where our burned-in captions are hidden. */
  caption_suppression_ranges: EditorRange[]
  /** Caption block center, as a fraction of output height. Null uses layout placement. */
  caption_y?: number | null
  /** Caption size, 1 = the preset's. */
  caption_scale?: number
  /** Burn the title in as a white card with black text. */
  title_card?: boolean
  /** Title card center, as a fraction of output height. Null uses layout placement. */
  title_y?: number | null
  /** Title card size, 1 = default. */
  title_scale?: number
  /** Title card face. Absent uses Montserrat. */
  title_font?: TitleFont
  /** Caption colour overrides, '#RRGGBB'. Absent uses the preset's. */
  caption_color?: string
  caption_highlight?: string
  caption_box?: string
  dismissed_camera_markers?: number[]
}
export interface EditorCandidate extends CandidateEdit {
  camera_scan?: CameraScan
  requires_visual_context?: boolean; score: number; reason: string; review: EditorReview | null; exports: number[]
  /** Main-owned: SHA-256 of the last baked render identity, so undoing "Refine again" can restore Baked. */
  baked_hash?: string
}
export interface EditorProject {
  version: 1; revision: number; title: string; duration_ms: number; width: number; height: number
  /** Main-owned media generation; absent on projects using the original source. */
  source_id?: string
  preview_id?: string
  frame_preview?: boolean
  /** Source and preview were deleted to save space; the project is read-only. */
  media_freed?: boolean
  /** The run's pacing; tight exports drop dead air inside the selected ranges. Missing means tight. */
  pacing?: 'tight' | 'natural'
  aspect_ratio: '9:16' | '16:9'; candidates: EditorCandidate[]
  transcript: { start_ms: number; end_ms: number; text: string }[]
}
export interface EditorProgress { phase: 'scan' | 'preview'; percent: number }
export type EditorOperation = 'save' | 'review' | 'export' | 'export-all' | 'replace-source' | 'scan-cameras' | 'follow-speaker'
export interface EditorBatch { completed: number; total: number; failed?: number }
export interface EditorSession {
  progress?: EditorProgress; project: EditorProject; sourcePath: string; previewPath: string
  operation?: EditorOperation | null; batch?: EditorBatch
  /** Bytes used by the editor's source and preview, freed by "Free editor media". */
  mediaBytes?: number
  /** Word timings for the caption preview; absent when transcript.json has none. */
  words?: EditorWords
}
/** Status counts for Library and Jobs, without sending the project to the renderer. */
export interface EditorProgressSummary {
  total: number; remaining: number; initialCandidate: number
  counts: Record<CandidateEdit['status'], number>
  /** Preview frame for a run without exports; null once media is freed. */
  previewPath: string | null; thumbnailMs: number; mediaFreed: boolean
  operation: EditorOperation | null; batch?: EditorBatch; progress?: EditorProgress
}

/** Main's stale-revision save error. The editor offers "Reload project" for it. */
export const EDITOR_REVISION_CONFLICT = 'This project changed. Reopen it before saving.'
/** Fixed worker failure codes (see bridge/editor_runner.py); no tool output crosses the bridge. */
export const editorErrorCodes = ['duration', 'geometry', 'audio', 'invalid', 'project_changed', 'invalid_edit', 'not_ready',
  'source_missing', 'source_incompatible', 'render_failed', 'scan_too_long', 'review_unavailable', 'engine_unavailable',
  'cancelled', 'timeout'] as const
export type EditorErrorCode = typeof editorErrorCodes[number]
export function isEditorErrorCode(value: unknown): value is EditorErrorCode {
  return typeof value === 'string' && (editorErrorCodes as readonly string[]).includes(value)
}

export const sourceReplacementErrors: Record<string, string> = {
  duration: 'The replacement has a different duration. Choose the exact same video with the same timing.',
  geometry: 'The replacement has different framing or orientation. Choose the same video and aspect ratio.',
  audio: 'The replacement has different audio availability. Choose the same video with the same audio.',
  invalid: 'Could not read the replacement video. Choose a playable video file.'
}

const logs = 'Details are in Help → Show Logs.'
const common: Partial<Record<EditorErrorCode, string>> = {
  project_changed: 'This project changed. Reopen the editor and try again.',
  source_missing: "The editor's source video is missing. Use Replace source with the original video, then try again.",
  source_incompatible: "FFmpeg could not read the editor's source video. Use Replace source with a playable copy of the same video.",
  engine_unavailable: 'The video engine could not start. Open Settings and run System check.'
}
/** A user-facing message for a failed editor worker. Only render setup problems point to System check. */
export function editorFailureMessage(action: Exclude<EditorOperation, 'save'>, code?: EditorErrorCode): string {
  if (action === 'replace-source') {
    if (code && Object.hasOwn(sourceReplacementErrors, code)) return sourceReplacementErrors[code]
    return code === 'cancelled' ? 'Source replacement cancelled. The previous source is still in use.'
      : code === 'timeout' ? 'Source replacement took too long and was stopped. The previous source is still in use.'
      : code === 'render_failed' ? `Could not prepare a preview from the replacement video. The previous source is still in use. ${logs}`
      : code && common[code] ? common[code]!
      : 'Source replacement stopped or failed. Reopen the editor to check its current source, then try again.'
  }
  if (action === 'scan-cameras') {
    return code === 'scan_too_long' ? 'This clip is too long to scan for camera changes. Try scanning a shorter clip. Your edits and previous markers are saved.'
      : code === 'cancelled' ? 'Camera scan cancelled. Your edits and previous markers are saved.'
      : code && common[code] ? `${common[code]} Your edits and previous markers are saved.`
      : 'Camera scan stopped or failed. Your edits and previous markers are saved. Try scanning a shorter clip.'
  }
  if (action === 'follow-speaker') {
    return code === 'cancelled' ? 'Speaker scan cancelled. Your framing is unchanged.'
      : code && common[code] ? `${common[code]} Your framing is unchanged.`
      : 'Speaker scan stopped or failed. Your framing is unchanged.'
  }
  if (action === 'review') {
    return code === 'review_unavailable' ? 'Jev could not review this edit. Check your OpenRouter key and connection, then try again. Your previous review is preserved.'
      : code === 'cancelled' ? 'Review cancelled. Your previous review is preserved.'
      : code && common[code] ? `${common[code]} Your previous review is preserved.`
      : 'Review stopped or failed. Your previous review is preserved. Try again.'
  }
  return code === 'render_failed' ? `Rendering failed. Your edits are saved. Try again, or simplify this clip's layouts. ${logs}`
    : code === 'invalid_edit' ? "This clip has an edit that can't be rendered. Check its cuts, layouts and captions, then try again."
    : code === 'not_ready' ? 'Mark this clip ready before baking it.'
    : code === 'timeout' ? 'Rendering took too long and was stopped. Your edits are saved.'
    : code === 'cancelled' ? 'Export cancelled.'
    : code && common[code] ? common[code]!
    : `Export stopped or failed. Your edits are saved. Try again. ${logs}`
}

export function editorProgress(candidates: Pick<CandidateEdit, 'status'>[]): { remaining: number; initialCandidate: number } {
  const unfinished = (c: Pick<CandidateEdit, 'status'>): boolean => c.status !== 'baked' && c.status !== 'discarded'
  const first = candidates.findIndex(unfinished)
  return { remaining: candidates.filter(unfinished).length,
    initialCandidate: Math.max(0, first >= 0 ? first : candidates.findIndex((c) => c.status === 'baked')) }
}

const fail = (): never => { throw new Error('Invalid editor project') }
const record = (x: unknown): Record<string, unknown> => x && typeof x === 'object' && !Array.isArray(x) ? x as Record<string, unknown> : fail()
const num = (x: unknown, lo: number, hi: number): number => typeof x === 'number' && Number.isFinite(x) && x >= lo && x <= hi ? x : fail()
const str = (x: unknown, max: number): string => typeof x === 'string' && x.length <= max ? x : fail()
/** Display text: clamp to `max` UTF-16 units without splitting a surrogate pair, never reject for length. */
export function clampText(x: unknown, max: number): string {
  if (typeof x !== 'string') return fail()
  if (x.length <= max) return x
  const end = /[\uD800-\uDBFF]/.test(x[max - 1] ?? '') ? max - 1 : max
  return x.slice(0, end)
}
const arr = (x: unknown, max: number): unknown[] => Array.isArray(x) && x.length <= max ? x : fail()
export function parseCandidateEdit(value: unknown, duration: number, transcriptCount = 100000): CandidateEdit {
  const v = record(value)
  const ranges = arr(v.ranges, 24).map((r) => {
    const a = arr(r, 2); if (a.length !== 2) fail()
    return [Math.round(num(a[0], 0, duration)), Math.round(num(a[1], 0, duration))] as EditorRange
  })
  if (!ranges.length || ranges.some(([a, b], i) => b - a < 100 || (i > 0 && a < ranges[i - 1][1]))) fail()
  const scenes = arr(v.scenes, 60).map((s): EditorScene => {
    const x = record(s)
    if (!['fill', 'split', 'fit', 'debate'].includes(x.layout as string)) fail()
    const crops = arr(x.crops, 2).map((r): Crop => {
      const c = arr(r, 4).map((n) => num(n, 0, 1))
      if (c.length !== 4 || c[2] < .01 || c[3] < .01 || c[0] + c[2] > 1.000001 || c[1] + c[3] > 1.000001) fail()
      return c as Crop
    })
    if (crops.length !== panelShares(x.layout as EditorScene['layout']).length) fail()
    const transition = x.transition_ms === undefined ? 0 : num(x.transition_ms, 0, 5000)
    if (transition > 0 && (transition < 100 || !Number.isInteger(transition))) fail()
    const swapped = x.swapped ?? false
    if (typeof swapped !== 'boolean' || (swapped && crops.length !== 2)) fail()
    const mirror = x.mirror === undefined ? [] : arr(x.mirror, 2)
    if (mirror.length && (mirror.length !== crops.length || mirror.some((m) => typeof m !== 'boolean'))) fail()
    return { at_ms: num(x.at_ms, 0, duration), layout: x.layout as EditorScene['layout'], crops,
      ...(transition ? { transition_ms: transition } : {}), ...(swapped ? { swapped: true } : {}),
      ...(mirror.some(Boolean) ? { mirror: mirror as boolean[] } : {}) }
  })
  if (!scenes.length || scenes[0].at_ms !== 0 || scenes.some((s, i) => i > 0 && s.at_ms <= scenes[i - 1].at_ms)) fail()
  if (scenes.some((s, i) => s.transition_ms && !canAnimateScene(scenes, i))) fail()
  const id = str(v.id, 64); if (!/^[a-zA-Z0-9_-]+$/.test(id)) fail()
  const title = clampText(v.title, 200); if (!title.trim()) fail()
  const caption_preset = str(v.caption_preset, 64); if (!/^[a-z0-9_-]+$/i.test(caption_preset)) fail()
  if (typeof v.captions !== 'boolean') fail()
  const status = v.status === undefined ? 'refining' : v.status
  if (!['refining', 'ready', 'baked', 'discarded'].includes(status as string)) fail()
  const seen = new Set<number>()
  const caption_edits = arr(v.caption_edits === undefined ? [] : v.caption_edits, 2000).map((item) => {
    const edit = record(item), segment = num(edit.segment, 0, transcriptCount - 1), text = str(edit.text, 2000)
    if (!Number.isInteger(segment) || seen.has(segment) || [...text].some((char) => char.charCodeAt(0) < 32 && !'\t\n\r'.includes(char))) fail()
    seen.add(segment)
    return { segment, text }
  }).sort((a, b) => a.segment - b.segment)
  const caption_suppression_ranges = arr(v.caption_suppression_ranges === undefined ? [] : v.caption_suppression_ranges, 200).map((r) => {
    const a = arr(r, 2); if (a.length !== 2) fail()
    return [Math.round(num(a[0], 0, duration)), Math.round(num(a[1], 0, duration))] as EditorRange
  })
  if (caption_suppression_ranges.some(([a, b], i) => b - a < 100 || (i > 0 && a < caption_suppression_ranges[i - 1][1]))) fail()
  if (v.title_card !== undefined && typeof v.title_card !== 'boolean') fail()
  if (v.title_font != null && !TITLE_FONTS.some((f) => f.id === v.title_font)) fail()
  const title_font = v.title_font != null && v.title_font !== 'montserrat' ? v.title_font as TitleFont : null
  const colors = Object.fromEntries((['caption_color', 'caption_highlight', 'caption_box'] as const).filter((k) => v[k] != null).map((k) => {
    if (typeof v[k] !== 'string' || !/^#[0-9a-f]{6}$/i.test(v[k] as string)) fail()
    return [k, (v[k] as string).toUpperCase()]
  }))
  const title_y = v.title_y == null ? null : num(v.title_y, .05, .95), title_scale = v.title_scale === undefined ? 1 : num(v.title_scale, .5, 2)
  const caption_scale = v.caption_scale === undefined ? 1 : num(v.caption_scale, .5, 2)
  return { id, title, ranges, scenes, captions: v.captions as boolean, caption_preset, video_speed: num(v.video_speed, 1, 2),
    status: status as CandidateEdit['status'], caption_edits, caption_suppression_ranges,
    caption_y: v.caption_y == null ? null : num(v.caption_y, .1, .9), ...(v.title_card ? { title_card: true } : {}),
    ...(title_y != null ? { title_y } : {}), ...(title_scale !== 1 ? { title_scale } : {}), ...(title_font ? { title_font } : {}), ...(caption_scale !== 1 ? { caption_scale } : {}), ...colors,
    ...(v.dismissed_camera_markers === undefined ? {} : { dismissed_camera_markers: [...new Set(arr(v.dismissed_camera_markers, 5000).map((t) => num(t, 0, duration)))].sort((a, b) => a - b) }) }
}
function question(value: unknown): EditorQuestion {
  const v = record(value)
  return { id: str(v.id, 64), prompt: clampText(v.prompt, 4000), yes: clampText(v.yes, 4000), no: clampText(v.no, 4000),
    probability: v.probability === null ? null : num(v.probability, 0, 1), threshold: num(v.threshold, 0, 1), status: str(v.status, 64) }
}
export function parseEditorProject(value: unknown): EditorProject {
  const v = record(value)
  if (v.source_id !== undefined && (typeof v.source_id !== 'string' || !/^[a-f0-9]{32}$/.test(v.source_id))) fail()
  if (v.preview_id !== undefined && (typeof v.preview_id !== 'string' || !/^[a-f0-9]{32}$/.test(v.preview_id))) fail()
  if (v.version !== 1 || !['9:16', '16:9'].includes(v.aspect_ratio as string)) fail()
  const duration = num(v.duration_ms, 100, 24 * 3600000)
  const transcript = arr(v.transcript, 100000).map((row) => { const t = record(row); return { start_ms: num(t.start_ms, 0, duration), end_ms: num(t.end_ms, 0, duration), text: clampText(t.text, 20000) } })
  const ids = new Set<string>()
  const candidates = arr(v.candidates, 100).map((item): EditorCandidate => {
    const c = record(item), edit = parseCandidateEdit(c, duration, transcript.length)
    if (ids.has(edit.id)) fail(); ids.add(edit.id)
    let review: EditorReview | null = null
    if (c.review) {
      const r = record(c.review)
      review = { signature: str(r.signature, 200000), reviewed_at: str(r.reviewed_at, 64), decision: str(r.decision, 64),
        questions: arr(r.questions, 32).map(question), cuts: arr(r.cuts, 24).map((cut) => {
          const x = record(cut), t = arr(x.interval, 2)
          if (t.length !== 2) fail()
          return { interval: [num(t[0], 0, duration), num(t[1], 0, duration)], questions: arr(x.questions, 16).map(question) }
        }) }
    }
    let camera_scan: CameraScan | undefined
    if (c.camera_scan !== undefined) {
      const scan = record(c.camera_scan), start_ms = num(scan.start_ms, 0, duration), end_ms = num(scan.end_ms, start_ms, duration)
      const frames = arr(scan.frames, 120000).map((t) => num(t, start_ms, end_ms))
      if (!frames.length || frames.some((t, i) => i > 0 && t <= frames[i - 1])) fail()
      const times = new Set(frames)
      const markers = arr(scan.markers, 5000).map((m) => { const x = record(m); return { at_ms: num(x.at_ms, start_ms, end_ms), score: num(x.score, 0, 1) } })
      if (markers.some((m, i) => !times.has(m.at_ms) || (i > 0 && m.at_ms <= markers[i - 1].at_ms))) fail()
      camera_scan = { start_ms, end_ms, frames, markers }
    }
    const baked_hash = typeof c.baked_hash === 'string' && /^[a-f0-9]{64}$/.test(c.baked_hash) ? c.baked_hash : undefined
    return { ...edit, ...(camera_scan ? { camera_scan } : {}), ...(baked_hash ? { baked_hash } : {}), requires_visual_context: c.requires_visual_context === true, score: num(c.score, 0, 100), reason: clampText(c.reason, 4000), review,
      exports: arr(c.exports, 1000).map((n) => num(n, 0, 999)) }
  })
  if (!candidates.length) fail()
  return { version: 1, revision: num(v.revision, 0, Number.MAX_SAFE_INTEGER), title: clampText(v.title, 1024), duration_ms: duration,
    width: num(v.width, 2, 16384), height: num(v.height, 2, 16384), aspect_ratio: v.aspect_ratio as EditorProject['aspect_ratio'], candidates,
    transcript, ...(v.preview_id ? { preview_id: v.preview_id as string } : {}), ...(v.frame_preview === true ? { frame_preview: true } : {}), ...(v.media_freed === true ? { media_freed: true } : {}), ...(v.pacing === 'natural' || v.pacing === 'tight' ? { pacing: v.pacing } : {}), ...(v.source_id ? { source_id: v.source_id as string } : {}) }
}
export function candidateEdit(c: CandidateEdit): CandidateEdit {
  const { id, title, ranges, scenes, captions, caption_preset, video_speed, status, caption_edits, caption_suppression_ranges = [], dismissed_camera_markers } = c
  return { id, title, ranges, scenes, captions, caption_preset, video_speed, status, caption_edits, caption_suppression_ranges, caption_y: c.caption_y ?? null, ...(c.caption_scale != null && c.caption_scale !== 1 ? { caption_scale: c.caption_scale } : {}), ...(c.title_card ? { title_card: true } : {}), ...(c.title_y != null ? { title_y: c.title_y } : {}), ...(c.title_scale != null && c.title_scale !== 1 ? { title_scale: c.title_scale } : {}), ...(c.title_font && c.title_font !== 'montserrat' ? { title_font: c.title_font } : {}), ...(c.caption_color ? { caption_color: c.caption_color } : {}), ...(c.caption_highlight ? { caption_highlight: c.caption_highlight } : {}), ...(c.caption_box ? { caption_box: c.caption_box } : {}), ...(dismissed_camera_markers ? { dismissed_camera_markers } : {}) }
}
export function renderEditKey(c: CandidateEdit): string {
  return JSON.stringify({ ...candidateEdit(c), status: undefined, dismissed_camera_markers: undefined })
}
export function refineEdit<T extends CandidateEdit>(c: T, patch: Partial<CandidateEdit>): T {
  const next = { ...c, ...patch }
  // A clip marked for export stays marked while you edit it; only a finished export goes stale.
  if (patch.status === undefined && c.status === 'baked' && renderEditKey(c) !== renderEditKey(next)) next.status = 'refining'
  return next
}
/** Caption and title header look, for "Apply to all clips". Unset colours stay unset so other clips use the preset's. */
export function clipStyle(c: CandidateEdit): Partial<CandidateEdit> {
  return { ...captionStyle(c), ...headerStyle(c) }
}
/** Caption look alone. */
export function captionStyle(c: CandidateEdit): Partial<CandidateEdit> {
  const { captions, caption_preset, caption_y, caption_scale, caption_color, caption_highlight, caption_box } = c
  return { captions, caption_preset, caption_y, caption_scale, caption_color, caption_highlight, caption_box }
}
/** Title header look alone; each clip keeps its own title text. */
export function headerStyle(c: CandidateEdit): Partial<CandidateEdit> {
  const { title_card, title_y, title_scale, title_font } = c
  return { title_card, title_y, title_scale, title_font }
}
export function editSignature(c: CandidateEdit): string {
  // Preserve existing review signatures for projects without motion.
  return JSON.stringify([c.title, c.ranges, c.scenes.map((s) => [s.at_ms, s.layout, s.crops, ...(s.transition_ms ? [s.transition_ms] : [])])])
}
export function sceneAt(c: CandidateEdit, t: number): EditorScene {
  return [...c.scenes].reverse().find((s) => s.at_ms <= t) ?? c.scenes[0]
}
/** Move a layout boundary without reordering scenes or changing their framing. */
export function retimeScene(scenes: EditorScene[], index: number, time: number, duration: number, frames?: number[]): EditorScene[] {
  if (index <= 0 || index >= scenes.length || !Number.isFinite(time)) return scenes
  const lo = scenes[index - 1].at_ms + 1
  const hi = Math.min(scenes[index + 1]?.at_ms ?? duration, duration) - 1
  if (hi < lo) return scenes
  const bounded = Math.max(lo, Math.min(hi, time))
  const at_ms = frames?.length ? snapFrame(frames, bounded) : Math.round(bounded)
  if (at_ms < lo || at_ms > hi) return scenes
  return at_ms === scenes[index].at_ms ? scenes : scenes.map((s, i) => i === index ? { ...s, at_ms } : s)
}
export function canAnimateScene(scenes: EditorScene[], index: number): boolean {
  return index > 0 && scenes[index].layout !== 'fit' && scenes[index - 1].layout === scenes[index].layout
    && !scenes[index - 1].swapped === !scenes[index].swapped
}
export function normalizeSceneTransitions(scenes: EditorScene[]): EditorScene[] {
  return scenes.map((s, i) => canAnimateScene(scenes, i) ? s : { ...s, transition_ms: undefined })
}
/** The same source-time smoothstep and interrupted-motion behavior as export. */
export function framingAt(c: CandidateEdit, t: number): EditorScene {
  let scene = c.scenes[0], from = scene.crops
  const cropsAt = (at: number): Crop[] => {
    const p = scene.transition_ms ? Math.max(0, Math.min(1, (at - scene.at_ms) / scene.transition_ms)) : 1
    const ease = p * p * (3 - 2 * p)
    return scene.crops.map((crop, j) => crop.map((n, k) => from[j][k] + (n - from[j][k]) * ease) as Crop)
  }
  for (let i = 1; i < c.scenes.length && c.scenes[i].at_ms <= t; i++) {
    const next = c.scenes[i]
    from = next.transition_ms && canAnimateScene(c.scenes, i) ? cropsAt(next.at_ms) : next.crops
    scene = next
  }
  return { ...scene, crops: cropsAt(t) }
}
export function trimRange(ranges: EditorRange[], index: number, edge: 0 | 1, time: number, duration: number): EditorRange[] {
  const next = ranges.map((r) => [...r] as EditorRange)
  const lo = edge === 0 ? (index ? ranges[index - 1][1] : 0) : ranges[index][0] + 100
  const hi = edge === 0 ? ranges[index][1] - 100 : (index + 1 < ranges.length ? ranges[index + 1][0] : duration)
  next[index][edge] = Math.max(lo, Math.min(hi, Math.round(time)))
  return next
}
/** CapCut Q/W: delete the part of the cut under the playhead to its left (edge 0) or right (edge 1). Null when the playhead is not inside a cut. */
export function deleteToPlayhead(ranges: EditorRange[], time: number, edge: 0 | 1, duration: number): EditorRange[] | null {
  const i = ranges.findIndex(([a, b]) => time > a && time < b)
  return i < 0 ? null : trimRange(ranges, i, edge, time, duration)
}
/** Play cuts only: skip gaps between cuts and `skips`, and stop at the end. Before the clip, or from its end onwards, play freely. */
export function cutPlayback(ranges: EditorRange[], t: number, playedFrom: number, skips: EditorRange[] = []): number | 'stop' | null {
  // 50 ms: a seek to the end can read back a hair earlier.
  if (t < ranges[0][0] || playedFrom >= ranges.at(-1)![1] - 50) return null
  const range = ranges.find(([, b]) => t < b)
  if (!range) return 'stop'
  if (t < range[0]) return range[0]
  return skips.find(([a, b]) => a <= t && t < b)?.[1] ?? null
}
/** Word timings per transcript line from the run's transcript.json: [word, start_ms, end_ms]. */
export type EditorWords = [string, number, number][][]
/** The caption group on screen at source time `t` and its active word, or null when none shows.
 * Mirrors the export: real word times (caption_transcript), groups as caption_generator._group_words.
 * Without word times, words are timed by length within the line. */
export function liveCaption(project: EditorProject, c: CandidateEdit, t: number, maxWords: number, timings?: EditorWords): { words: string[]; active: number } | null {
  if (!c.ranges.some(([a, b]) => a <= t && t < b) || (c.caption_suppression_ranges ?? []).some(([a, b]) => a <= t && t < b)) return null
  const index = project.transcript.findIndex((r) => r.start_ms <= t && t < r.end_ms)
  if (index < 0) return null
  const row = project.transcript[index], text = c.caption_edits.find((e) => e.segment === index)?.text ?? row.text
  const words = text.split(/\s+/).filter(Boolean)
  if (!words.length) return null
  const timed = timings?.[index] ?? []
  let starts: number[]
  if (timed.length === words.length) starts = timed.map((w) => w[1])
  else {
    // Corrections that change the word count are spread over the spoken line, like the export.
    const [a, b] = timed.length ? [timed[0][1], timed.at(-1)![2]] : [row.start_ms, row.end_ms]
    const weights = timed.length ? words.map(() => 1) : words.map((w) => w.length + 1)
    const total = weights.reduce((n, w) => n + w, 0)
    let sum = 0
    starts = weights.map((w) => { const at = a + (b - a) * sum / total; sum += w; return at })
  }
  let active = 0
  while (active < words.length - 1 && starts[active + 1] <= t) active++
  let first = 0
  for (let i = 0, count = 0; i < words.length; i++) {
    count++
    const last = words[i].at(-1)!
    if (i >= active) break
    if (count >= maxWords || '.!?'.includes(last) || (last === ',' && count >= 2)) { first = i + 1; count = 0 }
  }
  let end = first
  for (let count = 1; end < words.length - 1; end++, count++) {
    const last = words[end].at(-1)!
    if (count >= maxWords || '.!?'.includes(last) || (last === ',' && count >= 2)) break
  }
  return { words: words.slice(first, end + 1), active: active - first }
}
/** Pauses a tight export removes (clip_editor.compute_keep_intervals), so the preview can skip them too.
 * shortcut: uses transcript segments, not words, so pauses inside a segment still play; good enough for a preview. */
export function deadAirSkips(project: EditorProject, c: CandidateEdit): EditorRange[] {
  if (project.pacing === 'natural') return []
  const a = c.ranges[0][0], b = c.ranges.at(-1)![1]
  const spoken = project.transcript.filter((r) => r.end_ms > a && r.start_ms < b && r.text.trim()).map((r) => [Math.max(a, r.start_ms), Math.min(b, r.end_ms)] as EditorRange)
  if (!spoken.length) return []
  const skips: EditorRange[] = []
  if (spoken[0][0] - a > 320) skips.push([a, spoken[0][0] - 120])
  let end = spoken[0][1]
  for (const [s, e] of spoken.slice(1)) {
    if (s - end > 700) skips.push([end + 130, s - 130])
    end = Math.max(end, e)
  }
  if (b - end > 580) skips.push([end + 380, b])
  return skips
}
/** Debate: both speakers small on top, the current speaker large below (layout_renderer.DEBATE_TOP_RATIO). */
export const DEBATE_TOP_RATIO = .35
/** Each panel's share of the output height, top to bottom (Fit shows one whole frame). */
export function panelShares(layout: EditorScene['layout'], swapped = false): number[] {
  const shares = layout === 'split' ? [.5, .5] : layout === 'debate' ? [DEBATE_TOP_RATIO, 1 - DEBATE_TOP_RATIO] : [1]
  return swapped ? shares.reverse() : shares
}
export function defaultCrop(width: number, height: number, aspect: number, cx = .5, cy = .5, zoom = 1): Crop {
  const w = Math.min(1, height * aspect / width) / zoom, h = Math.min(1, width / aspect / height) / zoom
  return [Math.max(0, Math.min(1 - w, cx - w / 2)), Math.max(0, Math.min(1 - h, cy - h / 2)), w, h]
}
/** Resize in screen pixels, preserving proportions and the opposite corner. */
export function resizeCrop(crop: Crop, corner: CropCorner, dx: number, dy: number, frameWidth: number, frameHeight: number): Crop {
  const [x, y, w, h] = crop
  if (![dx, dy, frameWidth, frameHeight].every(Number.isFinite) || frameWidth <= 0 || frameHeight <= 0) return crop
  const sx = corner.endsWith('right') ? 1 : -1, sy = corner.startsWith('bottom') ? 1 : -1
  const ax = x + (sx < 0 ? w : 0), ay = y + (sy < 0 ? h : 0)
  const pixelW = w * frameWidth, pixelH = h * frameHeight
  // Project the pointer onto the corner's diagonal, so either axis can resize.
  const proposed = 1 + (sx * dx * pixelW + sy * dy * pixelH) / (pixelW * pixelW + pixelH * pixelH)
  const maximum = Math.min((sx > 0 ? 1 - ax : ax) / w, (sy > 0 ? 1 - ay : ay) / h)
  // Match the zoom slider's 4× limit, while retaining smaller legacy crops.
  const minimum = Math.max(.01 / w, .01 / h, Math.min(1, Math.min(1 / w, 1 / h) / 4))
  const scale = Math.max(minimum, Math.min(maximum, proposed))
  const nw = Math.max(.01, Math.min(1, w * scale)), nh = Math.max(.01, Math.min(1, h * scale))
  return [Math.max(0, Math.min(1 - nw, sx > 0 ? ax : ax - nw)), Math.max(0, Math.min(1 - nh, sy > 0 ? ay : ay - nh)), nw, nh]
}
export function editDuration(c: CandidateEdit): number { return c.ranges.reduce((n, [a, b]) => n + b - a, 0) / c.video_speed }

/** Lower bound with a small tolerance for browser media timestamps. */
function frameIndex(frames: number[], time: number): number {
  let lo = 0, hi = frames.length
  while (lo < hi) { const mid = (lo + hi) >>> 1; if (frames[mid] < time - .01) lo = mid + 1; else hi = mid }
  return lo
}
export function snapFrame(frames: number[], time: number): number {
  if (!frames.length || time < frames[0] || time > frames[frames.length - 1]) return time
  const i = frameIndex(frames, time)
  return i && time - frames[i - 1] < frames[i] - time ? frames[i - 1] : frames[i]
}
export function stepFrame(frames: number[], time: number, direction: -1 | 1): number {
  // Scans cover only part of the source. Continue with the pre-scan 30 fps
  // estimate outside that window, stopping at a known frame when re-entering.
  const fallback = time + direction * 1000 / 30
  if (!frames.length) return fallback
  if (time < frames[0] - .01) return direction > 0 ? Math.min(frames[0], fallback) : fallback
  if (time > frames[frames.length - 1] + .01) return direction < 0 ? Math.max(frames[frames.length - 1], fallback) : fallback
  const i = frameIndex(frames, time)
  if (direction < 0) return frames[i - 1] ?? fallback
  return frames[i + (Math.abs((frames[i] ?? Infinity) - time) < .01 ? 1 : 0)] ?? fallback
}
export function cameraMarkers(c: EditorCandidate, threshold: number): CameraScan['markers'] {
  const dismissed = new Set(c.dismissed_camera_markers ?? [])
  return (c.camera_scan?.markers ?? []).filter((m) => m.score >= threshold && !dismissed.has(m.at_ms) && c.ranges.some(([a, b]) => m.at_ms > a && m.at_ms < b))
}
