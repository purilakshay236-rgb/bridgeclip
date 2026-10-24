import { useEffect, useRef, useState, type RefObject } from 'react'
import type { CandidateEdit, EditorProject } from '../../shared/clip-editor'
import { captionAnchor } from '../lib/caption-preview'
import { DEBATE_TOP_RATIO, TITLE_FONTS, liveCaption, sceneAt, type EditorWords } from '../../shared/clip-editor'
import { captionPreviewPreset, textShadow } from './CaptionPresetPicker'

// Preset sizes in the export's 1080×1920 coordinate system. Browser font metrics
// differ from libass; this is a placement guide, not a pixel-exact render.
const SIZES: Record<string, number> = { pop: 84, spotlight: 80, impact: 124, glow: 80, boxed: 70, sweep: 76, editorial: 112, hype: 88, punch: 150, neon: 78, headline: 72, paper: 68, subtle: 70 }

/** The output frame's box inside the canvas's parent, for overlays. */
function useOutputBounds(canvas: RefObject<HTMLCanvasElement | null>, aspectRatio: string): { width: number; height: number; left: number; top: number } {
  const [bounds, setBounds] = useState({ width: 0, height: 0, left: 0, top: 0 })
  useEffect(() => {
    const element = canvas.current, parent = element?.parentElement
    if (!element || !parent) return
    const measure = (): void => {
      const box = element.getBoundingClientRect(), outer = parent.getBoundingClientRect()
      const aspect = element.width / element.height
      const width = Math.min(box.width, box.height * aspect), height = width / aspect
      setBounds({ width, height, left: box.left - outer.left + (box.width - width) / 2, top: box.top - outer.top + (box.height - height) / 2 })
    }
    const observer = new ResizeObserver(measure)
    observer.observe(element); observer.observe(parent); measure()
    return () => observer.disconnect()
  }, [canvas, aspectRatio])
  return bounds
}

/** Title card lines as rendering_service._build_title_card wraps them. */
export function titleLines(title: string): string[] {
  const words = title.split(/\s+/).filter(Boolean).join(' ').slice(0, 120).split(' ').filter(Boolean)
  if (words.length <= 3) return [words.join(' ')]
  // Every word is kept: dropping the end of a title can leave it ungrammatical.
  const count = Math.max(2, Math.ceil(words.length / 4)), lines: string[] = []
  for (let i = 0, start = 0; i < count; i++) {
    const end = start + Math.ceil((words.length - start) / (count - i))
    lines.push(words.slice(start, end).join(' ')); start = end
  }
  return lines
}
/** The title card's export scale: Debate clips get a smaller card (layout_renderer.DEBATE_TITLE_SCALE). */
function titleScale(candidate: CandidateEdit): number {
  return (candidate.title_scale ?? 1) * (candidate.scenes.some(s => s.layout === 'debate') ? .75 : 1)
}
/** Title card center as a fraction of output height: the editor's choice, else layout_renderer.title_y. */
export function titleCenter(project: EditorProject, candidate: CandidateEdit, time: number): number {
  if (candidate.title_y != null) return candidate.title_y
  const scale = titleScale(candidate), lines = titleLines(candidate.title).length
  if (project.aspect_ratio === '16:9') return (40 + (42 + (lines - 1) * 57 + 40) * scale / 2) / 1080
  const height = (42 + (lines - 1) * 57 + 40) * scale
  // Debate: just above the captions on the seam (layout_renderer.DEBATE_TITLE_GAP).
  if (sceneAt(candidate, time).layout === 'debate') return Math.max(10, 1920 * DEBATE_TOP_RATIO - 90 - height) / 1920 + height / 2 / 1920
  if (sceneAt(candidate, time).layout === 'fit') {
    const bar = (1920 - Math.min(1920, 1080 * project.height / project.width)) / 2
    if (bar > height + 20) return Math.max(10 + height / 2, bar / 2) / 1920
  }
  return (110 + height / 2) / 1920
}

export function EditorTitlePreview({ canvas, project, candidate, time, disabled, onMove, onDrag }: {
  canvas: RefObject<HTMLCanvasElement | null>; project: EditorProject; candidate: CandidateEdit; time: number
  disabled: boolean; onMove: (y: number, remember: boolean) => void; onDrag: (active: boolean) => void
}): React.JSX.Element | null {
  const bounds = useOutputBounds(canvas, project.aspect_ratio)
  const drag = useRef<{ pointer: number; start: number; y: number; changed: boolean } | null>(null)
  if (!candidate.title_card || !bounds.width) return null
  // Same 1080-wide units as the export's title card.
  const unit = bounds.width / (project.aspect_ratio === '16:9' ? 1920 : 1080) * titleScale(candidate)
  const y = titleCenter(project, candidate, time)
  const font = TITLE_FONTS.find((f) => f.id === candidate.title_font) ?? TITLE_FONTS[0]
  return <div className="editor-caption-overlay" style={bounds}>
    <button type="button" aria-label="Title header position" title="Drag up or down to place the title header. Arrow keys move it; Shift moves farther."
      className="editor-title-preview" disabled={disabled}
      style={{ top: `${y * 100}%`, fontSize: 42 * unit, fontFamily: font.family, fontWeight: font.weight, fontStyle: font.italic ? 'italic' : 'normal', padding: `${14 * unit}px ${28 * unit}px`, borderRadius: 16 * unit }}
      onPointerDown={e => {
        if (e.button !== 0) return
        e.preventDefault(); e.stopPropagation()
        drag.current = { pointer: e.pointerId, start: e.clientY, y, changed: false }
        e.currentTarget.focus({ preventScroll: true }); e.currentTarget.setPointerCapture(e.pointerId); onDrag(true)
      }}
      onPointerMove={e => {
        const state = drag.current
        if (!state || e.pointerId !== state.pointer || Math.abs(e.clientY - state.start) < 2 && !state.changed) return
        onMove(Math.max(.05, Math.min(.95, state.y + (e.clientY - state.start) / bounds.height)), !state.changed)
        state.changed = true
      }}
      onLostPointerCapture={() => { drag.current = null; onDrag(false) }}
      onKeyDown={e => {
        if (!['ArrowUp', 'ArrowDown'].includes(e.key)) return
        e.preventDefault(); e.stopPropagation()
        onMove(Math.max(.05, Math.min(.95, y + (e.key === 'ArrowUp' ? -1 : 1) * (e.shiftKey ? .05 : .01))), true)
      }}>
      {titleLines(candidate.title).map((line, i) => <span key={i}>{line}</span>)}
    </button>
  </div>
}

/** Live captions on the output preview; with `guide`, a placeholder shows between spoken lines. */
export function EditorCaptionPreview({ canvas, project, words: timings, candidate, time: pausedTime, video, guide, disabled, onMove, onDrag }: {
  canvas: RefObject<HTMLCanvasElement | null>; project: EditorProject; words?: EditorWords; candidate: CandidateEdit; time: number; video: RefObject<HTMLVideoElement | null>
  guide: boolean; disabled: boolean; onMove: (y: number, remember: boolean) => void; onDrag: (active: boolean) => void
}): React.JSX.Element | null {
  const bounds = useOutputBounds(canvas, project.aspect_ratio)
  const drag = useRef<{ pointer: number; start: number; y: number; changed: boolean } | null>(null)
  // The editor's time state only moves while paused; follow the video here so only this overlay re-renders.
  const [playing, setPlaying] = useState<number | null>(null)
  useEffect(() => {
    let frame = 0
    const tick = (): void => { const v = video.current; setPlaying(v && !v.paused ? Math.round(v.currentTime * 30) / 30 * 1000 : null); frame = requestAnimationFrame(tick) }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [video])
  const time = playing ?? pausedTime
  const preset = captionPreviewPreset(candidate.caption_preset), landscape = project.aspect_ratio === '16:9'
  const anchor = captionAnchor(project, candidate, time)
  const scale = bounds.width / (landscape ? 1920 : 1080)
  const size = (SIZES[preset.id] ?? 84) * (landscape ? .65 : 1) * scale * .8 * (candidate.caption_scale ?? 1)
  const shadow = textShadow(preset, scale * 3)
  const primary = candidate.caption_color ?? preset.primary, highlight = candidate.caption_highlight ?? preset.highlight
  const pill = preset.pill && (candidate.caption_box ?? preset.pill)
  const live = candidate.captions ? liveCaption(project, candidate, time, preset.maxWords ?? 3, timings) : null
  const { words, active } = live ?? { words: ['Captions', 'go', 'here'], active: 1 }
  if (!candidate.captions || !bounds.width || (!live && !guide)) return null
  return <div className="editor-caption-overlay" style={bounds}>
    <button type="button" aria-label="Subtitle position" title="Drag up or down to position subtitles. Arrow keys move them; Shift moves farther."
      className="editor-caption-preview" disabled={disabled}
      style={{ top: `${anchor.y * 100}%`, transform: `translate(-50%, ${anchor.bottom ? '-100%' : '-50%'})`, fontSize: size,
        fontFamily: preset.font, fontWeight: preset.weight, fontStyle: preset.italic ? 'italic' : 'normal',
        textTransform: preset.uppercase ? 'uppercase' : 'none', color: primary, textShadow: shadow }}
      onPointerDown={e => {
        if (e.button !== 0) return
        e.preventDefault(); e.stopPropagation()
        const box = e.currentTarget.getBoundingClientRect()
        drag.current = { pointer: e.pointerId, start: e.clientY, y: anchor.y - (anchor.bottom ? box.height / bounds.height / 2 : 0), changed: false }
        e.currentTarget.focus({ preventScroll: true }); e.currentTarget.setPointerCapture(e.pointerId); onDrag(true)
      }}
      onPointerMove={e => {
        const state = drag.current
        if (!state || e.pointerId !== state.pointer || Math.abs(e.clientY - state.start) < 2 && !state.changed) return
        onMove(Math.max(.1, Math.min(.9, state.y + (e.clientY - state.start) / bounds.height)), !state.changed)
        state.changed = true
      }}
      onLostPointerCapture={() => { drag.current = null; onDrag(false) }}
      onKeyDown={e => {
        if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return
        e.preventDefault(); e.stopPropagation()
        const direction = e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 1
        const center = anchor.y - (anchor.bottom ? e.currentTarget.getBoundingClientRect().height / bounds.height / 2 : 0)
        onMove(Math.max(.1, Math.min(.9, center + direction * (e.shiftKey ? .05 : .01))), true)
      }}>
      <span className="editor-caption-words" style={{ background: preset.plate }}>
        {words.map((word, i) => <span key={i} style={{ color: i === active ? highlight : primary,
          background: i === active ? pill || undefined : undefined,
          padding: pill ? '0 .15em' : undefined, borderRadius: '.15em',
          visibility: preset.future === 'hide' && i > active ? 'hidden' : undefined,
          opacity: preset.future === 'dim' && i > active ? preset.dimOpacity ?? .45 : undefined,
          textShadow: i === active && preset.glow ? `${shadow}, 0 0 ${size / 3}px ${preset.glow}` : undefined }}>{word}</span>)}
      </span>
    </button>
  </div>
}
