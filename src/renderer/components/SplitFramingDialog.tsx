import { useEffect, useId, useRef, useState } from 'react'
import { Dialog, DialogFooter } from './ui/Dialog'
import { Button } from './ui/Button'
import { getApi } from '../lib/ipc'
import { cn } from '../lib/utils'
import type { Job } from '../store/use-job-store'

/** Each panel is half of a 1080x1920 frame. */
const PANEL_ASPECT = 1080 / 960
const MIN_WIDTH = 0.05
const LABELS = ['Top', 'Bottom']

type Crop = number[]
type Drag = { index: number; mode: 'move' | 'resize'; x: number; y: number; start: Crop }

/** Keep a crop inside the frame at the panel aspect (source pixels: w*W / (h*H) = PANEL_ASPECT). */
function clampCrop([x, y, w]: Crop, frameAspect: number): Crop {
  const heightFor = (width: number): number => width * frameAspect / PANEL_ASPECT
  w = Math.max(MIN_WIDTH, Math.min(w, 1, PANEL_ASPECT / frameAspect))
  const h = heightFor(w)
  return [Math.max(0, Math.min(x, 1 - w)), Math.max(0, Math.min(y, 1 - h)), w, h]
}

/** The crop scaled to fill a box, as CSS background geometry. */
function panelStyle(image: string, [x, y, w, h]: Crop): React.CSSProperties {
  return {
    backgroundImage: `url("${image}")`,
    backgroundSize: `${100 / w}% ${100 / h}%`,
    backgroundPosition: `${w < 1 ? (x / (1 - w)) * 100 : 0}% ${h < 1 ? (y / (1 - h)) * 100 : 0}%`
  }
}

/**
 * Split style asks once per job: place the two panels on one source frame and
 * every clip renders with that framing. The engine waits until this is answered.
 */
export function SplitFramingDialog({ job }: { job: Job }): React.JSX.Element | null {
  const framing = job.framing
  const titleId = useId()
  const frameRef = useRef<HTMLDivElement>(null)
  const [crops, setCrops] = useState<Crop[]>(() => framing?.crops.map((c) => [...c]) ?? [])
  const [drag, setDrag] = useState<Drag | null>(null)
  const [sending, setSending] = useState(false)
  const frameAspect = framing ? framing.width / framing.height : 16 / 9

  useEffect(() => { frameRef.current?.querySelector<HTMLElement>('[data-crop]')?.focus() }, [])

  if (!framing) return null

  const update = (index: number, crop: Crop): void =>
    setCrops((current) => current.map((c, i) => (i === index ? clampCrop(crop, frameAspect) : c)))

  const onPointerMove = (event: React.PointerEvent): void => {
    const rect = frameRef.current?.getBoundingClientRect()
    if (!drag || !rect) return
    const dx = (event.clientX - drag.x) / rect.width
    const dy = (event.clientY - drag.y) / rect.height
    const [x, y, w, h] = drag.start
    update(drag.index, drag.mode === 'move' ? [x + dx, y + dy, w, h] : [x, y, w + dx, h])
  }

  const onKeyDown = (index: number, event: React.KeyboardEvent): void => {
    const step = 0.01
    const [x, y, w, h] = crops[index]
    const moves: Record<string, Crop> = event.shiftKey
      ? { ArrowUp: [x, y, w + 2 * step, h], ArrowDown: [x, y, w - 2 * step, h] }
      : { ArrowLeft: [x - step, y, w, h], ArrowRight: [x + step, y, w, h], ArrowUp: [x, y - step, w, h], ArrowDown: [x, y + step, w, h] }
    if (!moves[event.key]) return
    event.preventDefault()
    update(index, moves[event.key])
  }

  const submit = (value: Crop[] | null): void => {
    setSending(true)
    void getApi().job.setFraming(job.id, value).catch(() => {}).finally(() => setSending(false))
  }

  return (
    <Dialog aria-labelledby={titleId} panelClassName="max-w-[860px]">
      <div className="overflow-y-auto px-5 pb-5 pt-5">
        <h2 id={titleId} className="text-base font-semibold text-ink">Set the split framing</h2>
        <p className="mt-1.5 text-sm text-ink-muted">
          Drag each box over a person; drag its corner to zoom. Every clip in this run uses this framing.
        </p>
        <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start">
          <div
            ref={frameRef}
            className="relative min-w-0 flex-1 touch-none select-none overflow-hidden rounded-xl bg-black"
            style={{ aspectRatio: `${framing.width} / ${framing.height}` }}
            onPointerMove={onPointerMove}
            onPointerUp={() => setDrag(null)}
            onPointerCancel={() => setDrag(null)}
          >
            <img src={framing.image} alt="A frame from the source video" className="absolute inset-0 h-full w-full" draggable={false} />
            {crops.map(([x, y, w, h], i) => (
              <div
                key={i}
                data-crop
                tabIndex={0}
                role="group"
                aria-label={`${LABELS[i]} panel. Arrow keys move it; Shift with up or down arrow zooms out or in.`}
                className={cn(
                  'absolute cursor-move rounded-md border-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-white',
                  i === 0 ? 'border-accent bg-accent/10' : 'border-amber-400 bg-amber-400/10'
                )}
                style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }}
                onKeyDown={(e) => onKeyDown(i, e)}
                onPointerDown={(e) => {
                  e.currentTarget.parentElement?.setPointerCapture(e.pointerId)
                  setDrag({ index: i, mode: 'move', x: e.clientX, y: e.clientY, start: crops[i] })
                }}
              >
                <span className="absolute left-1 top-1 rounded bg-black/60 px-1.5 text-2xs text-white">{LABELS[i]}</span>
                <span
                  aria-hidden
                  className="absolute -bottom-1.5 -right-1.5 h-3 w-3 cursor-nwse-resize rounded-sm border border-black/40 bg-white"
                  onPointerDown={(e) => {
                    e.stopPropagation()
                    e.currentTarget.closest('[data-crop]')?.parentElement?.setPointerCapture(e.pointerId)
                    setDrag({ index: i, mode: 'resize', x: e.clientX, y: e.clientY, start: crops[i] })
                  }}
                />
              </div>
            ))}
          </div>
          <div className="mx-auto w-[140px] shrink-0">
            <span className="eyebrow">Preview</span>
            <div className="mt-2 flex aspect-[9/16] flex-col overflow-hidden rounded-lg bg-black" aria-hidden>
              {crops.map((crop, i) => <div key={i} className="flex-1 bg-no-repeat" style={panelStyle(framing.image, crop)} />)}
            </div>
          </div>
        </div>
      </div>
      <DialogFooter>
        <Button disabled={sending} onClick={() => submit(null)}>Use detected framing</Button>
        <Button variant="primary" loading={sending} disabled={sending} onClick={() => submit(crops)}>Render clips</Button>
      </DialogFooter>
    </Dialog>
  )
}
