import { twitchVodId } from '../shared/video-source'
import { constants, closeSync, existsSync, fstatSync, lstatSync, mkdirSync, openSync, readdirSync, readFileSync, realpathSync, renameSync, unlinkSync, writeFileSync } from 'fs'
import { basename, isAbsolute, join, relative, sep } from 'path'
import { randomUUID } from 'crypto'

export type StoredRunStatus = 'running' | 'completed' | 'failed' | 'cancelled'

export interface RunRecord {
  jobId: string
  startedAt: string
  finishedAt: string | null
  sourceLabel: string
  status: StoredRunStatus
  errorMessage: string | null
  failureCode?: string | null
  failureStage?: string | null
  httpStatus?: number | null
}

const RUN_FILE = 'run-history.json'
const MAX_RECORD_BYTES = 16 * 1024
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** A run's folder: named by its job id while it runs, then after its video (nameRunAfterVideo). */
function runDirectory(baseDir: string, folder: string): string {
  if (!UUID.test(folder) && !validFolderName(folder)) throw new Error('Invalid run identifier')
  return join(baseDir, folder)
}

function validFolderName(name: string): boolean {
  return name.length > 0 && name.length <= 255 && name !== '.' && name !== '..' && !name.startsWith('.') && !/[/\\\0]/.test(name)
}

function checkedDirectory(baseDir: string, folder: string): string {
  const dir = runDirectory(baseDir, folder)
  const stat = lstatSync(dir)
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Invalid run directory')
  const rel = relative(realpathSync(baseDir), realpathSync(dir))
  if (isAbsolute(rel) || rel === '..' || rel.startsWith(`..${sep}`)) throw new Error('Run is outside the output folder')
  return dir
}

function sourceLabel(source: string): string {
  const twitchId = twitchVodId(source)
  if (twitchId) return `Twitch VOD · ${twitchId}`.slice(0, 160)
  let label: string
  try {
    const url = new URL(source)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error('Local source')
    const host = url.hostname.toLowerCase()
    const videoId = (host === 'youtube.com' || host === 'www.youtube.com' || host === 'm.youtube.com') && url.pathname === '/watch'
      ? url.searchParams.get('v') : null
    label = videoId && /^[a-zA-Z0-9_-]{11}$/.test(videoId) ? `YouTube · ${videoId}` : host
  } catch {
    label = basename(source)
  }
  return Array.from(label, (character) => {
    const code = character.charCodeAt(0)
    return code < 32 || code === 127 ? ' ' : character
  }).join('').trim().slice(0, 160) || 'Video source'
}

function validDate(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 40 && Number.isFinite(Date.parse(value))
}

/** The run record in `folder`, a job id or the video-named folder it was renamed to. */
export function readRunRecord(baseDir: string, folder: string): RunRecord | null {
  let fd: number | null = null
  try {
    const dir = checkedDirectory(baseDir, folder)
    const file = join(dir, RUN_FILE)
    fd = openSync(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
    const stat = fstatSync(fd)
    if (!stat.isFile() || stat.size > MAX_RECORD_BYTES) return null
    const fileStat = lstatSync(file)
    if (fileStat.isSymbolicLink() || fileStat.dev !== stat.dev || fileStat.ino !== stat.ino) return null
    const data: unknown = JSON.parse(readFileSync(fd, 'utf8'))
    if (!data || typeof data !== 'object') return null
    const record = data as Partial<RunRecord>
    if (typeof record.jobId !== 'string' || !UUID.test(record.jobId) || (UUID.test(folder) && record.jobId !== folder) || !validDate(record.startedAt) ||
        (record.finishedAt !== null && !validDate(record.finishedAt)) ||
        typeof record.sourceLabel !== 'string' || record.sourceLabel.length > 160 ||
        !['running', 'completed', 'failed', 'cancelled'].includes(record.status ?? '') ||
        (record.errorMessage !== null && (typeof record.errorMessage !== 'string' || record.errorMessage.length > 300)) ||
        (record.failureCode != null && (typeof record.failureCode !== 'string' || !/^[a-z]+(?:[._][a-z]+)*$/.test(record.failureCode) || record.failureCode.length > 64)) ||
        (record.failureStage != null && !['setup', 'download', 'transcription', 'planning', 'rendering', 'saving', 'uploading'].includes(record.failureStage)) ||
        (record.httpStatus != null && (!Number.isInteger(record.httpStatus) || record.httpStatus < 100 || record.httpStatus > 599))) return null
    return record as RunRecord
  } catch {
    return null
  } finally {
    if (fd !== null) closeSync(fd)
  }
}

function writeRunRecord(baseDir: string, record: RunRecord, folder = record.jobId): void {
  const dir = checkedDirectory(baseDir, folder)
  const temp = join(dir, `${RUN_FILE}.${randomUUID()}.tmp`)
  try {
    writeFileSync(temp, JSON.stringify(record), { encoding: 'utf8', mode: 0o600, flag: 'wx' })
    renameSync(temp, join(dir, RUN_FILE))
  } finally {
    try { unlinkSync(temp) } catch { /* The rename already moved it. */ }
  }
}

export function createRunRecord(baseDir: string, jobId: string, source: string): void {
  mkdirSync(runDirectory(baseDir, jobId), { recursive: true, mode: 0o700 })
  writeRunRecord(baseDir, {
    jobId,
    startedAt: new Date().toISOString(),
    finishedAt: null,
    sourceLabel: sourceLabel(source),
    status: 'running',
    errorMessage: null
  })
}

export function finishRunRecord(baseDir: string, jobId: string, status: Exclude<StoredRunStatus, 'running'>, errorMessage: string | null = null,
  details: Pick<RunRecord, 'failureCode' | 'failureStage' | 'httpStatus'> = {}): void {
  const previous = readRunRecord(baseDir, jobId)
  if (!previous || previous.status !== 'running') return
  writeRunRecord(baseDir, { ...previous, status, finishedAt: new Date().toISOString(), errorMessage, ...details })
}

/** The folder of run `jobId` inside baseDir: its id, or the video-named folder it was renamed to. */
export function runFolder(baseDir: string, jobId: string): string | null {
  if (!UUID.test(jobId)) return null
  if (existsSync(join(baseDir, jobId))) return join(baseDir, jobId)
  let names: string[]
  try { names = readdirSync(baseDir) } catch { return null }
  const name = names.find((folder) => validFolderName(folder) && !UUID.test(folder) && readRunRecord(baseDir, folder)?.jobId === jobId)
  return name ? join(baseDir, name) : null
}

/** A folder name from a video title: no path characters or yt-dlp's trailing [id], at most 100 characters. */
export function videoFolderName(title: string): string {
  const name = Array.from(title.replace(/\.(mp4|m4v|mkv|webm|avi|mov|flv)$/i, '').replace(/\s*\[[A-Za-z0-9_-]{6,}\]\s*$/, ''), (character) => {
    const code = character.charCodeAt(0)
    return code < 32 || code === 127 || '/\\:*?"<>|'.includes(character) ? ' ' : character
  }).join('').replace(/\s+/g, ' ').trim().replace(/^[.\s]+|[.\s]+$/g, '')
  return Array.from(name).slice(0, 100).join('').trim() || 'Video'
}

/**
 * Rename a finished run's folder to its video's title ("Title", then "Title 2", "Title 3" for the same
 * video again) and point job_output.json's file links at the new folder. Returns the new folder, or the
 * old one when it could not be renamed.
 */
export function nameRunAfterVideo(baseDir: string, jobId: string, title: string): string {
  const from = join(baseDir, jobId)
  try {
    checkedDirectory(baseDir, jobId)
    const base = videoFolderName(title)
    let name = base
    for (let n = 2; existsSync(join(baseDir, name)); n++) name = `${base} ${n}`
    const to = join(baseDir, name)
    const outputPath = join(from, 'job_output.json')
    const original = existsSync(outputPath) ? readFileSync(outputPath, 'utf8') : null
    renameSync(from, to)
    if (original !== null) {
      const relink = (value: unknown): unknown => typeof value === 'string'
        ? value.replace(new RegExp(`^(file://)?${from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?=[/\\\\])`), (_, scheme = '') => `${scheme}${to}`)
        : Array.isArray(value) ? value.map(relink)
        : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, relink(item)])) : value
      const temp = join(to, `job_output.json.${randomUUID()}.tmp`)
      try {
        writeFileSync(temp, JSON.stringify(relink(JSON.parse(original)), null, 2), { encoding: 'utf8', mode: 0o600, flag: 'wx' })
        renameSync(temp, join(to, 'job_output.json'))
      } finally {
        try { unlinkSync(temp) } catch { /* The rename already moved it. */ }
      }
    }
    return to
  } catch {
    return from
  }
}
