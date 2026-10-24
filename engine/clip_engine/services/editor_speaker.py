"""Editor "Follow speaker": Full-frame layouts on whoever is speaking, one layout per speaker turn.

Faces are found with the layout analyzer's YuNet model. Speech is split into pieces at
pauses between words, and each piece is given to the lower or the higher voice by its pitch,
learned across all clips of the source. Each voice belongs to the face whose mouth moves
more during it (mouth measured against the same face's eye region so head movement does
not count), and keeps that seat in clips where its mouth can't be read. Pieces whose pitch
is in between keep the current framing. Without two distinct voices, the face whose mouth
moves most during a piece speaks it.
"""
from statistics import median

from .media_process import MEDIA_INPUT_OPTIONS, media_process

FPS = 8
WIDTH = 960
MAX_TRACK_GAP_MS = 1000
# Short interjections ("yeah", "right") keep the current framing.
MIN_TURN_MS = 1200
# Present in this share of a line's frames to be its speaker.
MIN_PRESENCE = .3
MIN_ACTIVITY = .3
# Mouth movement is compared with the same face's usual level (beards, side-on faces and
# distance change the scale), never below this floor so a nearly still face isn't inflated.
ACTIVITY_FLOOR = .5
# Lines are judged in pieces split at pauses between words: unlabelled transcripts can run
# one line across both speakers.
PAUSE_MS = 300
MAX_PIECE_MS = 2000
AUDIO_RATE = 16000
# A voice is tied to a face only when that face's mouth clearly moves more during it.
MIN_VOICE_LINK = .4
# Two voices only when their typical pitches differ this much (one person's intonation spans
# less), and a piece within VOICE_BAND of the midpoint belongs to neither.
VOICE_RATIO = 1.4
VOICE_BAND = 1.1
# Pitch at least this far from the midpoint is decisive and teaches the timbre model, which decides
# the pieces in between (or without pitch) when it places them at least TIMBRE_SURE of the way to a voice.
TRAIN_BAND = 1.3
MIN_TRAIN = 4
TIMBRE_SHRINK = .3
TIMBRE_SURE = .4
TIMBRE_VETO = .6  # timbre this far toward the other voice withholds a decisive pitch's call
MOUTH_WEIGHT = .5
# When the source's voices can't tell a clip's two people apart (two men among a compilation's many
# guests), pieces where one seat's mouth moves at least this much more than the other's teach the clip's
# own timbre model. The two seats are the main faces on screen through at least SEAT_STEADY of the clip,
# the smaller at least SEAT_PAIR of the larger's size.
MOUTH_SEED = .8
# ...and only if their voices then tell those pieces apart better than chance (leave-one-out, one-sided
# binomial p below this): a listener's nods teach nothing in a monologue, two real voices do. Pitch counts
# as PITCH_WEIGHT timbre coefficients there: two men's ranges overlap, but one is usually a little higher.
TWO_VOICES_P = .01
PITCH_WEIGHT = 4
SEAT_STEADY = .5
SEAT_PAIR = .6
MIN_VOICE_SHARE = .05  # of the speech a voice must hold
SOURCE_CHUNK_MS = 10 * 60 * 1000
MIN_VOICED = 20  # 10 ms pitch frames a piece needs to be judged by pitch
OCTAVE_PICK = .9  # of the best lag's correlation, for a shorter lag to be the fundamental
QUIET_SHARE = 1e-3  # frames 30 dB below the recording's loud speech are too quiet to read
# A voice's face is the one within this distance of where that voice usually sits.
SEAT_RADIUS = .15
# Of the faces near a seat, only those at least this share of the largest one's size.
SEAT_SIZE = .85
MIN_RELATIVE_SIZE = .7
# A new face must move its mouth this much more than the current speaker to take over.
SWITCH_MARGIN = 1.1
# Over a whole run of one voice, a face away from that voice's seat takes the run when its mouth moves
# this much more than the seat's: two guests of one voice range, the second standing elsewhere.
RUN_TAKEOVER = 1.5
RUN_GAP_MS = 1500
RUN_MIN_MS = 4000  # a shorter run is a piece or two, where one fidget can outweigh speech
# Turns whose crops land this close together are one framing.
SAME_SPOT = .1
OUT_W, OUT_H = 1080, 1920  # 9:16 output, for the Debate layout's panel shapes
FACE_Y = .38  # face centre sits this far down a cropped frame, like Automatic's fill crop
MAX_SCENES = 60
# A composite seam: a column step this sharp (grey levels) on this share of rows of a sampled frame, and
# while the composite is on screen at least SEAM_MIN_ROWS (a tent pole or door frame is partial)...
SEAM_STEP = 20
SEAM_ROWS = .5
SEAM_MIN_ROWS = .4
SEAM_EVERY_MS = 1000
# ...in the same column in at least this many sampled frames, and this share of them.
SEAM_FRAMES = 3
SEAM_SHARE = .25
# In a composite panel, a face at least this share of the panel's largest face can be a speaker.
PANEL_SUBJECT = .9
# Within a turn, glide to re-centre when the speaker's face drifts this share of the crop from its centre;
# each glide takes PAN_MS and the camera then holds at least PAN_HOLD_MS.
CENTER_ZONE = .1
PAN_MS = 500
PAN_HOLD_MS = 250


def track_faces(frames):
    """frames: [(t_ms, [(cx, cy, w, h, activity)])] -> tracks of [(t_ms, cx, cy, w, h, activity)]."""
    tracks = []
    for t, faces in frames:
        used = set()
        for face in sorted(faces, key=lambda f: -f[2] * f[3]):
            best, best_dist = None, None
            for i, track in enumerate(tracks):
                last = track[-1]
                if i in used or t - last[0] > MAX_TRACK_GAP_MS:
                    continue
                dist = ((face[0] - last[1]) ** 2 + (face[1] - last[2]) ** 2) ** .5
                if dist <= max(face[2], last[3]) and (best_dist is None or dist < best_dist):
                    best, best_dist = i, dist
            if best is None:
                tracks.append([])
                best = len(tracks) - 1
            used.add(best)
            tracks[best].append((t, *face))
    # Rejoin one person's pieces after a cut away and back: same spot and size, never on screen together.
    merged = []
    for track in sorted(tracks, key=lambda tr: tr[0][0]):
        cx, cy, w = (median(s[k] for s in track) for k in (1, 2, 3))
        for other in merged:
            ox, oy, ow = (median(s[k] for s in other) for k in (1, 2, 3))
            if other[-1][0] < track[0][0] + MAX_TRACK_GAP_MS and abs(cx - ox) < max(w, ow) and abs(cy - oy) < max(w, ow) and .7 < w / ow < 1.4:
                other.extend(track)
                break
        else:
            merged.append(list(track))
    return merged


def speech_pieces(lines, words):
    """Split lines [(start, end)] at pauses between words [(start, end)], or every MAX_PIECE_MS."""
    pieces = []
    for start, end in lines:
        inside = [(max(start, a), min(end, b)) for a, b in words if b > start and a < end]
        if not inside:
            pieces.append((start, end))
            continue
        a, b = inside[0]
        for ws, we in inside[1:]:
            if ws - b >= PAUSE_MS or we - a > MAX_PIECE_MS:
                pieces.append((a, b))
                a = ws
            b = max(b, we)
        pieces.append((a, b))
    return pieces


def _piece_stats(tracks, lines, edges=()):
    """Per piece: (start, end, eligible faces, raw mouth activity, activity on each face's own scale, face sizes).

    A face's size is the larger of its size now and over its whole track: a subject who turns away for
    a moment stays the subject, and a cut to their close-up makes them the largest.

    edges: composite seams over time (panel_edges). Each panel frames its own subject, so there only faces
    about as large as their panel's largest can speak: the rest is the crowd behind the subject,
    whose fidgeting can outweigh a mouth hidden by a handheld mic.
    """
    scale = {i: max(ACTIVITY_FLOOR, sum(s[5] for s in track) / len(track)) for i, track in enumerate(tracks) if track}
    span = max(1, (lines[-1][1] - lines[0][0]) * FPS / 1000) if lines else 1
    # The size of the people on screen through the clip, so a lone background face never counts.
    main = max((median(s[4] for s in track) for track in tracks if len(track) >= MIN_PRESENCE * span), default=0)
    track_size = [median(s[4] for s in track) if track else 0 for track in tracks]
    stats = []
    for start, end in lines:
        expected = max(1, (end - start) * FPS / 1000)
        present = {i: [s for s in track if start <= s[0] < end] for i, track in enumerate(tracks)}
        present = {i: samples for i, samples in present.items() if len(samples) >= MIN_PRESENCE * expected}
        raw = {i: sum(s[5] for s in samples) / len(samples) for i, samples in present.items()}
        size = {i: max(track_size[i], median(s[4] for s in samples)) for i, samples in present.items()}
        # Small background faces are noisy and rarely the speaker.
        eligible = [i for i in present if size[i] >= MIN_RELATIVE_SIZE * max(main, *size.values())]
        seams = edges_at(edges, (start + end) / 2)
        if seams:
            panel = {i: sum(median(s[1] for s in present[i]) >= e for e in seams) for i in eligible}
            largest = {}
            for i in eligible:
                largest[panel[i]] = max(largest.get(panel[i], 0), size[i])
            eligible = [i for i in eligible if size[i] >= PANEL_SUBJECT * largest[panel[i]]]
        stats.append((start, end, eligible, raw, {i: raw[i] / scale[i] for i in present}, size))
    return stats


def voice_links(stats, voices):
    """{(face, voice): how much more the face's mouth moves during that voice than the other}."""
    def mean_score(i, v):
        # Duration-weighted, so a half-second "Right?" can't outweigh a long answer.
        rows = [(p[1] - p[0], p[4][i]) for p, pv in zip(stats, voices) if pv == v and i in p[2]]
        return sum(d * x for d, x in rows) / sum(d for d, _ in rows) if rows else None
    link = {}
    # Only faces the size of the people on screen: a small crowd face's noise can look like speech.
    for i in {i for p in stats for i in p[2]}:
        for v in (0, 1):
            own, other = mean_score(i, v), mean_score(i, 1 - v)
            if own is not None and other is not None:
                link[i, v] = own - other
    return link


def _cx(track):
    return median(s[1] for s in track)


def seat_mouths(tracks, lines, seats, edges=()):
    """Per piece, how much more the mouth at voice 1's seat moves than at voice 0's (each on its own
    scale), or None when the two seats aren't both on screen."""
    if len(seats) != 2:
        return [None] * len(lines)
    result = []
    for _, _, eligible, _, scores, _ in _piece_stats(tracks, lines, edges):
        at = [[scores[i] for i in eligible if abs(_cx(tracks[i]) - seats[v]) < SEAT_RADIUS] for v in (0, 1)]
        result.append(max(at[1]) - max(at[0]) if at[0] and at[1] else None)
    return result


def voice_seats(tracks, lines, voices, edges=()):
    """[(voice, face centre x)] for the face most clearly tied to each voice in this clip."""
    best = {}
    for (i, v), value in voice_links(_piece_stats(tracks, lines, edges), voices).items():
        if value >= MIN_VOICE_LINK and value > best.get(v, (0, None))[0]:
            best[v] = (value, i)
    return [(v, _cx(tracks[i])) for v, (_, i) in best.items()]


def run_takeovers(tracks, stats, voices, seats):
    """Per piece, the face that takes its voice's run from the seat (RUN_TAKEOVER), or None.

    A seat says where a voice usually sits, but two people can share one voice range (two women
    answering one man). Mouth movement summed over the run, not one piece, decides between them:
    wind in hair or a nod moves a listener's mouth region for a moment, a speaker's for the whole run.
    """
    result = [None] * len(stats)
    k = 0
    while k < len(stats):
        # A run is one voice until the other voice speaks or a pause long enough for someone else to start.
        v, end = voices[k], k + 1
        while (end < len(stats) and v is not None and voices[end] != 1 - v
               and stats[end][0] - stats[end - 1][1] < RUN_GAP_MS):
            end += 1
        if v in seats and stats[end - 1][1] - stats[k][0] >= RUN_MIN_MS:
            run = stats[k:end]
            seat_score = seat_raw = 0.0
            challengers = {}
            for _, _, eligible, raw, scores, size in run:
                # Never the other voice's person, nor a crowd face smaller than this side's people.
                side = [i for i in eligible if 1 - v not in seats or abs(_cx(tracks[i]) - seats[1 - v]) >= SEAT_RADIUS]
                if not side:
                    continue
                side = [i for i in side if size[i] >= SEAT_SIZE * max(size[j] for j in side)]
                at_seat = [i for i in side if abs(_cx(tracks[i]) - seats[v]) < SEAT_RADIUS]
                if at_seat:
                    i = max(at_seat, key=lambda i: scores[i])
                    seat_score, seat_raw = seat_score + scores[i], seat_raw + raw[i]
                for i in side:
                    if i not in at_seat:
                        score, moved = challengers.get(i, (0.0, 0.0))
                        challengers[i] = (score + scores[i], moved + raw[i])
            if challengers:
                best = max(challengers, key=lambda i: challengers[i][0])
                score, moved = challengers[best]
                if score > 0 and score >= RUN_TAKEOVER * seat_score and moved >= seat_raw:
                    result[k:end] = [best] * (end - k)
        k = end
    return result


def seat_positions(seen):
    """{voice: usual face centre x} from voice_seats of one or more clips."""
    seats = {v: median(x for sv, x in seen if sv == v) for v in (0, 1) if any(sv == v for sv, _ in seen)}
    if len(seats) == 2 and abs(seats[0] - seats[1]) < SEAT_RADIUS:
        return {}  # both voices seem to sit in one place: seats can't tell them apart
    return seats


def speaker_turns(tracks, lines, voices=None, seats=None, edges=()):
    """[(start_ms, track)] for speech pieces [(start_ms, end_ms)].

    voices: 0, 1 or None per piece; seats: {voice: usual face centre x} from all clips.
    """
    stats = _piece_stats(tracks, lines, edges)
    voices = voices or [None] * len(stats)
    seats = seats or {}
    link = voice_links(stats, voices)
    takeover = run_takeovers(tracks, stats, voices, seats)
    turns = []
    # How long each voice keeps talking from a piece on: short pieces back to back in one voice are a
    # turn, not interjections ("Really?" alone is skipped; "Did I say... creatures?" in two pieces is not).
    run_end = [e for _, e, *_ in stats]
    for k in range(len(stats) - 2, -1, -1):
        if voices[k] is not None and voices[k + 1] == voices[k]:
            run_end[k] = run_end[k + 1]
    for (start, end, eligible, raw, scores, size), voice, until, taker in zip(stats, voices, run_end, takeover):
        current = turns[-1][1] if turns else None
        if turns and until - start < MIN_TURN_MS:
            continue
        best = taker
        if best is None and voice is not None and eligible:
            near = [i for i in eligible if voice in seats and abs(_cx(tracks[i]) - seats[voice]) < SEAT_RADIUS]
            linked = [i for i in eligible if link.get((i, voice), 0) >= MIN_VOICE_LINK]
            # The other voice's seat is known, so this voice is someone else (a handheld mic can hide a mouth).
            elsewhere = [i for i in eligible if 1 - voice in seats and abs(_cx(tracks[i]) - seats[1 - voice]) >= SEAT_RADIUS]
            if not elsewhere and (taken := [i for i in eligible if link.get((i, 1 - voice), 0) >= MIN_VOICE_LINK]):
                elsewhere = [i for i in eligible if i not in taken]
            if near:
                # Several faces can sit near a seat (a crowd behind a guest). Only the near faces about
                # as large as the biggest one can be the guest; of those, the one whose mouth follows
                # this voice, else the largest. Never merely the nearest.
                near = [i for i in near if size[i] >= SEAT_SIZE * max(size[j] for j in near)]
                best = max(near, key=lambda i: (link.get((i, voice), 0) >= MIN_VOICE_LINK, size[i]))
            elif linked:
                best = max(linked, key=lambda i: link[i, voice])
            elif elsewhere:
                best = max(elsewhere, key=lambda i: size[i])
        if best is None:
            if any(v is not None for v in voices):
                # Two voices are known but this piece is unclear: keep the framing. The first framing
                # waits for a clear piece too (it still starts the clip), so mouth movement never picks it.
                continue
            best = max((i for i in eligible if raw[i] > MIN_ACTIVITY), key=lambda i: scores[i], default=None)
            # Switch only when both the face's own scale and raw movement favour it: a quiet face's
            # head turn can beat its own average while the current speaker is still clearly talking.
            if best is None or (turns and (scores[best] < SWITCH_MARGIN * scores.get(current, 0) or raw[best] < raw.get(current, 0))):
                continue
        if not turns or current != best:
            turns.append((start, best))
    while len(turns) > MAX_SCENES:
        # Merge the shortest turn into the one before it.
        lengths = [turns[i + 1][0] - turns[i][0] for i in range(1, len(turns) - 1)]
        i = 1 + lengths.index(min(lengths))
        del turns[i]
        if i < len(turns) and turns[i][1] == turns[i - 1][1]:
            del turns[i]
    return turns


def piece_pitch(samples, start_ms, pieces):
    """Median voice pitch (Hz) per piece by autocorrelation, None with too little voiced sound."""
    import numpy as np
    if samples is None:
        return [None] * len(pieces)
    win, hop, lo, hi = 640, 160, AUDIO_RATE // 400, AUDIO_RATE // 70
    # Sound counts relative to how loud this recording's speech is: quiet sources (a distant handheld
    # mic, -55 dB) would otherwise fall under a fixed gate and leave whole lines without a voice.
    energy = np.convolve(samples ** 2, np.ones(win) / win, 'valid')[::hop] if len(samples) >= win else np.zeros(0)
    gate = max(1e-9, float(np.percentile(energy, 95)) * QUIET_SHARE) if len(energy) else 1e-5
    pitches = []
    for start, end in pieces:
        x = samples[max(0, int((start - start_ms) * AUDIO_RATE / 1000)):int((end - start_ms) * AUDIO_RATE / 1000)]
        n = 1 + (len(x) - win) // hop
        if n < MIN_VOICED:
            pitches.append(None)
            continue
        frames = np.stack([x[k * hop:k * hop + win] for k in range(n)])
        frames = frames - frames.mean(1, keepdims=True)
        frames = frames[(frames ** 2).mean(1) > gate]
        ac = np.fft.irfft(np.abs(np.fft.rfft(frames, 2 * win)) ** 2)[:, :win]
        # The fundamental is the shortest lag that correlates nearly as well as the best: multiples of
        # the period correlate too, and taking one reads a raised or high voice an octave low.
        band = ac[:, lo - 1:hi + 1]
        peaks = (band[:, 1:-1] > band[:, :-2]) & (band[:, 1:-1] >= band[:, 2:])
        strong = peaks & (band[:, 1:-1] >= OCTAVE_PICK * band[:, 1:-1].max(1, keepdims=True))
        lag = lo + np.where(strong.any(1), strong.argmax(1), band[:, 1:-1].argmax(1))
        voiced = ac[np.arange(len(ac)), lag] > .5 * ac[:, 0]
        pitches.append(float(np.median(AUDIO_RATE / lag[voiced])) if voiced.sum() >= MIN_VOICED else None)
    return pitches


def voice_labels(pitches, weights, timbres=None, mouths=None):
    """0 (lower voice), 1 (higher voice) or None per piece; all None unless the pitches form two distinct voices.

    timbres: piece_timbre per piece. Pitch alone fails where the voices overlap (a man's voice raised in
    an argument reaches a woman's range), so the pieces pitch places clearly teach a timbre model, which
    then decides the pieces whose pitch is in between or unreadable. mouths (seat_mouths) adds, for those
    pieces only, how much more the voice-1 seat's mouth moves than the voice-0 seat's.
    """
    import math
    known = [(math.log(p), w) for p, w in zip(pitches, weights) if p]
    if len(known) < 4:
        return [None] * len(pitches)
    # Start from the extremes: when one person talks most of a clip, percentiles both land in their
    # voice and the other speaker is never found.
    low, high = min(x for x, _ in known), max(x for x, _ in known)
    for _ in range(30):
        mid = (low + high) / 2
        groups = [[(x, w) for x, w in known if (x >= mid) == g] for g in (False, True)]
        if not groups[0] or not groups[1]:
            return [None] * len(pitches)
        low, high = (sum(x * w for x, w in g) / sum(w for _, w in g) for g in groups)
    total = sum(w for _, w in known)
    # A voice is more than one stray piece (a pitch-tracking octave slip, a laugh).
    if any(len(g) < 2 or sum(w for _, w in g) < MIN_VOICE_SHARE * total for g in groups):
        return [None] * len(pitches)
    if high - low < math.log(VOICE_RATIO):
        return [None] * len(pitches)
    mid, band = (low + high) / 2, math.log(VOICE_BAND)
    labels = [None if not p or abs(math.log(p) - mid) < band else int(math.log(p) > mid) for p in pitches]
    clear = [None if not p or abs(math.log(p) - mid) < math.log(TRAIN_BAND) else int(math.log(p) > mid) for p in pitches]
    model = taught_model(timbres, clear) if timbres else None
    if model is None:
        return labels
    result = []
    for label, sure, timbre, mouth in zip(labels, clear, timbres, mouths or [None] * len(labels)):
        side = model(timbre) if timbre is not None else None
        if side is None:
            result.append(label)
        elif sure is not None:
            # Pitch this far out is decisive; strong disagreement is a tracking slip or two people at once.
            result.append(None if (side > 0) != bool(sure) and abs(side) >= TIMBRE_VETO else sure)
        else:
            # In between, timbre decides: a raised voice keeps its timbre while its pitch moves. Whose mouth
            # moves weighs in, capped: a listener nods and mouths along, a mic hides a speaker's mouth.
            if mouth is not None:
                side += MOUTH_WEIGHT * max(-2, min(2, mouth))
            result.append(int(side > 0) if abs(side) >= TIMBRE_SURE else None)
    return result


def main_seats(tracks):
    """{0: left x, 1: right x} of the two main people on screen through the clip, or {}."""
    longest = max((len(t) for t in tracks), default=0)
    steady = sorted(((median(s[4] for s in t), _cx(t)) for t in tracks if len(t) >= SEAT_STEADY * longest), reverse=True)
    if not steady:
        return {}
    size, x = steady[0]
    other = next((ox for os, ox in steady[1:] if os >= SEAT_PAIR * size and abs(ox - x) >= SEAT_RADIUS), None)
    return {} if other is None else dict(enumerate(sorted((x, other))))


def clip_voices(tracks, lines, timbres, pitches, edges=()):
    """(voices, seats) learned from this clip alone, for when the source's voices don't tell its two
    people apart: a compilation's host meets many guests, and two men share a pitch range. Pieces where
    one seat's mouth clearly moves more teach a timbre model of the clip's two voices; timbre, with
    mouths capped as in voice_labels, decides the rest. None without two people on screen or enough
    clear pieces of each."""
    seats = main_seats(tracks)
    if not seats:
        return None
    timbres = with_pitch(timbres, pitches)
    mouths = seat_mouths(tracks, lines, seats, edges)
    seeds = [None if m is None or abs(m) < MOUTH_SEED else int(m > 0) for m in mouths]
    model = taught_model(timbres, seeds)
    if model is None or not two_voices(timbres, seeds):
        return None
    voices = []
    for timbre, mouth in zip(timbres, mouths):
        side = (model(timbre) if timbre is not None else 0) + MOUTH_WEIGHT * max(-2, min(2, mouth or 0))
        voices.append(int(side > 0) if abs(side) >= TIMBRE_SURE else None)
    return voices, seats


def with_pitch(timbres, pitches):
    """Timbres with log pitch appended PITCH_WEIGHT times (the mean pitch where a piece has none)."""
    import math
    import numpy as np
    known = [math.log(p) for p in pitches if p]
    mean = sum(known) / len(known) if known else 0
    return [None if t is None else np.append(t, [math.log(p) if p else mean] * PITCH_WEIGHT) for t, p in zip(timbres, pitches)]


def two_voices(timbres, labels):
    """Whether timbre tells the labelled pieces apart better than chance: each is predicted by a model
    taught without it."""
    from math import comb
    hits = tries = 0
    for i, (timbre, label) in enumerate(zip(timbres, labels)):
        if timbre is None or label is None:
            continue
        model = taught_model(timbres, [None if j == i else v for j, v in enumerate(labels)])
        if model is not None:
            tries += 1
            hits += (model(timbre) > 0) == bool(label)
    return tries > 0 and sum(comb(tries, k) for k in range(hits, tries + 1)) / 2 ** tries < TWO_VOICES_P


def _mel_dct():
    import numpy as np
    nfft, bands = 512, 26
    mel, hz = (lambda f: 2595 * np.log10(1 + f / 700)), (lambda m: 700 * (10 ** (m / 2595) - 1))
    bins = np.floor((nfft + 1) * hz(np.linspace(mel(80), mel(7600), bands + 2)) / AUDIO_RATE).astype(int)
    bank = np.zeros((bands, nfft // 2 + 1))
    for m in range(1, bands + 1):
        a, c, b = bins[m - 1], bins[m], bins[m + 1]
        bank[m - 1, a:c] = (np.arange(a, c) - a) / max(1, c - a)
        bank[m - 1, c:b] = (b - np.arange(c, b)) / max(1, b - c)
    dct = np.cos(np.pi / bands * (np.arange(bands) + .5)[None, :] * np.arange(1, 14)[:, None])
    return bank, dct


def piece_timbre(samples, start_ms, pieces):
    """Per piece, the voice's timbre (mean and spread of its MFCCs over the louder half of the sound), or None."""
    import numpy as np
    if samples is None:
        return [None] * len(pieces)
    bank, dct = _mel_dct()
    win, hop = 400, 160
    result = []
    for start, end in pieces:
        x = samples[max(0, int((start - start_ms) * AUDIO_RATE / 1000)):int((end - start_ms) * AUDIO_RATE / 1000)]
        n = 1 + (len(x) - win) // hop
        if n < MIN_VOICED:
            result.append(None)
            continue
        frames = np.stack([x[k * hop:k * hop + win] for k in range(n)]) * np.hamming(win)
        energy = (frames ** 2).mean(1)
        frames = frames[energy > np.percentile(energy, 50)]
        mfcc = np.log(np.abs(np.fft.rfft(frames, 512)) ** 2 @ bank.T + 1e-10) @ dct.T
        result.append(np.concatenate([mfcc.mean(0), mfcc.std(0)]))
    return result


def timbre_model(timbres, labels):
    """A two-voice classifier (shrunk linear discriminant) taught by the pieces `labels` places, or None.
    It maps a timbre to a side: about -1 for voice 0, +1 for voice 1."""
    import numpy as np
    known = [(t, v) for t, v in zip(timbres, labels) if t is not None and v is not None]
    if min(sum(v == 0 for _, v in known), sum(v == 1 for _, v in known)) < MIN_TRAIN:
        return None
    X, y = np.array([t for t, _ in known]), np.array([v for _, v in known])
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z = (X - mu) / sd
    m0, m1 = Z[y == 0].mean(0), Z[y == 1].mean(0)
    within = np.cov(np.concatenate([Z[y == 0] - m0, Z[y == 1] - m1]).T)
    w = np.linalg.solve((1 - TIMBRE_SHRINK) * within + TIMBRE_SHRINK * np.eye(len(m0)), m1 - m0)
    centre, half = (m0 + m1) @ w / 2, max(1e-6, abs((m1 - m0) @ w) / 2)
    return lambda t: float((((t - mu) / sd) @ w - centre) / half)


def taught_model(timbres, labels):
    """timbre_model refit without the teaching pieces it places on the other side (a pitch slip, two
    people talking at once), so one mislabelled piece can't pull the voices together; or None."""
    model = timbre_model(timbres, labels)
    if model is None:
        return None
    agreed = [None if v is None or t is None or (model(t) > 0) != bool(v) else v for v, t in zip(labels, timbres)]
    return timbre_model(timbres, agreed) or model


_speech_cache = {}


def source_speech(source, lines, words):
    """(pieces, pitches, timbres) for everything said in the source. Voices are learned from the whole
    conversation: a clip where one person barely speaks would otherwise misplace the line between voices.
    Audio is read in SOURCE_CHUNK_MS chunks to bound memory; the last source's result is kept."""
    import os
    try:
        key = (os.path.realpath(source), os.path.getmtime(source), tuple(lines), tuple(words))
    except OSError:
        key = None
    if key is not None and key in _speech_cache:
        return _speech_cache[key]
    pieces = speech_pieces(lines, words)
    pitches, timbres, k = [], [], 0
    while k < len(pieces):
        j = k + 1
        while j < len(pieces) and pieces[j][1] - pieces[k][0] <= SOURCE_CHUNK_MS:
            j += 1
        start, chunk = pieces[k][0], pieces[k:j]
        audio = read_audio(source, start, max(e for _, e in chunk))
        pitches += piece_pitch(audio, start, chunk)
        timbres += piece_timbre(audio, start, chunk)
        k = j
    result = pieces, pitches, timbres
    if key is not None:
        _speech_cache.clear()
        _speech_cache[key] = result
    return result


def within(pieces, start_ms, end_ms):
    """[(index, piece clipped to the window)] for the pieces that overlap [start_ms, end_ms)."""
    return [(k, (max(start_ms, s), min(end_ms, e))) for k, (s, e) in enumerate(pieces) if min(end_ms, e) - max(start_ms, s) >= 100]


def read_audio(source, start_ms, end_ms):
    """Mono float samples at AUDIO_RATE for the source window."""
    import numpy as np
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{start_ms / 1000:.3f}', *MEDIA_INPUT_OPTIONS, '-i', str(source),
           '-t', f'{(end_ms - start_ms) / 1000:.3f}', '-vn', '-ac', '1', '-ar', str(AUDIO_RATE), '-f', 's16le', 'pipe:1']
    with media_process(cmd, timeout=10 * 60) as (proc, _):
        data = proc.stdout.read()
        if proc.wait():
            raise RuntimeError('Speaker audio decoding failed')
    return np.frombuffer(data[:len(data) // 2 * 2], np.int16).astype(np.float32) / 32768


def debate_top(tracks, w, h):
    """The Debate layout's top crop: as wide as its panel allows, centred between the two main people."""
    from .layout_renderer import DEBATE_TOP_RATIO, even
    aspect = OUT_W / even(OUT_H * DEBATE_TOP_RATIO)
    # The people on screen longest and largest, not a passing crowd face.
    main = sorted((t for t in tracks if t), key=lambda t: -len(t) * median(s[4] for s in t))[:2]
    cx = sum(_cx(t) for t in main) / len(main) if main else .5
    cw, ch = min(1, h * aspect / w), min(1, w / aspect / h)
    return [max(0, min(1 - cw, cx - cw / 2)), (1 - ch) / 2, cw, ch]


def debate_bottom(samples, turn, w, h):
    """The Debate layout's bottom crop: the speaker's head and shoulders, sized over the turn, placed at `samples`."""
    from .layout_renderer import DEBATE_TOP_RATIO, MAX_UPSCALE, PERSON_CROP_FACE_MULT, PERSON_FACE_Y, even
    panel_h = OUT_H - even(OUT_H * DEBATE_TOP_RATIO)
    aspect = OUT_W / panel_h
    ch = min(1, max(median(s[4] for s in turn) * PERSON_CROP_FACE_MULT, panel_h / MAX_UPSCALE / h))
    cw = min(1, ch * h * aspect / w)
    ch = cw * w / aspect / h
    cx, cy = median(s[1] for s in samples), median(s[2] for s in samples)
    return [max(0, min(1 - cw, cx - cw / 2)), max(0, min(1 - ch, cy - ch * PERSON_FACE_Y)), cw, ch]


def follow_scenes(tracks, turns, size, end_ms, debate=None, edges=()):
    """Editor scenes on each turn's speaker, panning when they move: a Full frame crop of `size` (w, h),
    or with debate=(top crop, source w, source h) the Debate layout's close-up under a fixed top crop.
    Close-ups stay inside the composite panel (between the seams of the `edges` timeline) that holds the speaker."""
    def crop(samples, turn):
        cx, seams = median(s[1] for s in samples), edges_at(edges, samples[0][0])
        if debate:
            return keep_in_panel(debate_bottom(samples, turn, *debate[1:]), cx, seams)
        cw, ch = size
        cy = median(s[2] for s in samples)
        return keep_in_panel([max(0, min(1 - cw, cx - cw / 2)), max(0, min(1 - ch, cy - ch * FACE_Y)), cw, ch], cx, seams)

    def scene(at, close_up, **extra):
        if debate:
            return {'at_ms': at, 'layout': 'debate', 'crops': [debate[0], close_up], **extra}
        return {'at_ms': at, 'layout': 'fill', 'crops': [close_up], **extra}
    def offset(a, b):
        """How far apart two crops are, as a share of the crop's size."""
        return max(abs(a[0] - b[0]) / a[2], abs(a[1] - b[1]) / a[3])
    scenes, pans = [], []
    for j, (start, i) in enumerate(turns):
        stop = turns[j + 1][0] if j + 1 < len(turns) else end_ms
        samples = [s for s in tracks[i] if start <= s[0] < stop]
        if not samples:  # off screen for this turn: where they usually are
            samples = tracks[i]
        current = crop(samples[:FPS // 2 + 1], samples)
        last = scenes[-1]['crops'][-1] if scenes else None
        if last and abs(last[0] - current[0]) < SAME_SPOT and abs(last[1] - current[1]) < SAME_SPOT:
            # The same person again (their face was briefly lost): glide back to centre rather than cut.
            if offset(last, current) >= CENTER_ZONE and start + PAN_MS <= stop:
                scenes.append(scene(start, current, transition_ms=PAN_MS))
                pans.append((offset(last, current), len(scenes) - 1))
            else:
                current = last
        else:
            scenes.append(scene(0 if j == 0 else start, current))
        # A virtual camera: hold while the face stays near the centre, glide to re-centre when it drifts,
        # aiming at where the face is over the next half second so the glide doesn't trail behind.
        moved_at = start
        for k in range(1, len(samples)):
            at = samples[k][0]
            if at - moved_at < PAN_MS + PAN_HOLD_MS or at + PAN_MS > stop:
                continue
            target = crop(samples[k:k + FPS // 2 + 1], samples)
            if offset(current, target) >= CENTER_ZONE:
                scenes.append(scene(at, target, transition_ms=PAN_MS))
                pans.append((offset(current, target), len(scenes) - 1))
                current, moved_at = target, at
    # Speaker changes always fit; the smallest pans give way.
    drop = {index for _, index in sorted(pans)[:max(0, len(scenes) - MAX_SCENES)]}
    return [scene for index, scene in enumerate(scenes) if index not in drop]


def _patch_change(previous, gray, x, y, w, h):
    import cv2
    import numpy as np
    H, W = gray.shape
    x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(W, int(x + w)), min(H, int(y + h))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0.0
    a = cv2.resize(previous[y0:y1, x0:x1], (24, 12)).astype(np.float32)
    b = cv2.resize(gray[y0:y1, x0:x1], (24, 12)).astype(np.float32)
    return float(np.abs(a - b).mean())


def _activity(previous, gray, row):
    """Mouth movement minus eye-region movement for one YuNet detection row."""
    fw, fh = float(row[2]), float(row[3])
    (rex, rey), (lex, ley), (rmx, rmy), (lmx, lmy) = row[4:6], row[6:8], row[10:12], row[12:14]
    mw = max(abs(lmx - rmx) * 1.4, fw * .35)
    mouth = _patch_change(previous, gray, (rmx + lmx) / 2 - mw / 2, (rmy + lmy) / 2 - fh * .15, mw, fh * .3)
    eyes = _patch_change(previous, gray, (rex + lex) / 2 - mw / 2, (rey + ley) / 2 - fh * .15, mw, fh * .3)
    return max(0.0, mouth - eyes)


def detect_frames(source, start_ms, end_ms, src_w, src_h, progress=None):
    """[(t_ms, [(cx, cy, w, h, activity)])] sampled at FPS over the source window."""
    import cv2
    import numpy as np
    from .layout_analyzer import FACE_SCORE_THRESHOLD, LayoutAnalyzer
    width = WIDTH if src_w > WIDTH else src_w // 2 * 2
    height = round(src_h * width / src_w / 2) * 2
    detector = cv2.FaceDetectorYN.create(LayoutAnalyzer()._model_path(), '', (width, height), FACE_SCORE_THRESHOLD, .3, 50)
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{start_ms / 1000:.3f}', *MEDIA_INPUT_OPTIONS, '-i', str(source),
           '-t', f'{(end_ms - start_ms) / 1000:.3f}', '-an', '-vf', f'fps={FPS},scale={width}:{height}',
           '-f', 'rawvideo', '-pix_fmt', 'bgr24', 'pipe:1']
    size, frames, previous, n = width * height * 3, [], None, 0
    total = max(1, (end_ms - start_ms) * FPS / 1000)
    with media_process(cmd, timeout=30 * 60) as (proc, _):
        while len(data := proc.stdout.read(size)) == size:
            image = np.frombuffer(data, np.uint8).reshape(height, width, 3)
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            _, rows = detector.detect(image)
            faces = []
            for row in rows if rows is not None else []:
                if float(row[14]) >= FACE_SCORE_THRESHOLD:
                    x, y, w, h = (float(v) for v in row[:4])
                    faces.append(((x + w / 2) / width, (y + h / 2) / height, w / width, h / height,
                                  _activity(previous, gray, row) if previous is not None else 0.0))
            frames.append((start_ms + round(n * 1000 / FPS), faces))
            previous, n = gray, n + 1
            if progress and n % FPS == 0:
                progress(min(99, round(100 * n / total)))
        if proc.wait():
            raise RuntimeError('Speaker scan decoding failed')
    return frames


def panel_edges(source, start_ms, end_ms):
    """[(t_ms, seam x positions)] about once a second: hard vertical edges running the full frame height,
    the seams of a side-by-side composite. A crop that crosses one shows a sliver of the other panel.
    Timed, because a source can cut from its composite to a full-screen shot and back."""
    import numpy as np
    width, height = 480, 270
    count = max(2, round((end_ms - start_ms) / SEAM_EVERY_MS))
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{start_ms / 1000:.3f}', *MEDIA_INPUT_OPTIONS, '-i', str(source),
           '-t', f'{(end_ms - start_ms) / 1000:.3f}', '-an', '-vf', f'fps={count * 1000 / max(1, end_ms - start_ms)},scale={width}:{height}',
           '-frames:v', str(count), '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1']
    with media_process(cmd, timeout=5 * 60) as (proc, _):
        data = proc.stdout.read()
        if proc.wait():
            raise RuntimeError('Panel scan decoding failed')
    frames = np.frombuffer(data[:len(data) // (width * height) * width * height], np.uint8).reshape(-1, height, width).astype(np.int16)
    if not len(frames):
        return []
    # Per frame and column: the share of rows with a sharp step to the next column.
    steps = (np.abs(np.diff(frames, axis=2)) > SEAM_STEP).mean(1)
    middle = range(int(.15 * width), int(.85 * width))
    strong = [[x for x in middle if f[x] >= SEAM_ROWS and f[x] >= f[x - 2:x + 3].max()] for f in steps]
    # A seam stays put: the same column in several frames. A door frame or pole is rarely this sharp from
    # top to bottom, and never in the same column while the camera or people move.
    seams = [x for x in middle if sum(any(abs(x - y) <= 1 for y in row) for row in strong) >= max(SEAM_FRAMES, SEAM_SHARE * len(steps))
             and steps[:, x].mean() >= steps[:, max(0, x - 1)].mean() and steps[:, x].mean() >= steps[:, x + 1].mean()]
    step_ms = (end_ms - start_ms) / len(frames)
    return [(round(start_ms + (k + .5) * step_ms), [(x + 1) / width for x in seams if steps[k, x] >= SEAM_MIN_ROWS])
            for k in range(len(frames))]


def edges_at(edges, t):
    """Seam positions at source time t from panel_edges' timeline (the nearest sample)."""
    return min(edges, key=lambda e: abs(e[0] - t))[1] if edges else []


def keep_in_panel(crop, cx, edges):
    """crop [x, y, w, h] slid sideways to stay inside the composite panel holding cx, when it fits there."""
    lo = max((e for e in edges if e <= cx), default=0)
    hi = min((e for e in edges if e > cx), default=1)
    if crop[2] > hi - lo:
        return crop
    return [max(lo, min(hi - crop[2], crop[0])), *crop[1:]]


def uncovered(span, covered):
    """Parts of span [a, b) outside the covered spans."""
    a, b = span
    gaps = []
    for s, e in sorted(covered):
        if e <= a or s >= b:
            continue
        if s > a:
            gaps.append([a, s])
        a = max(a, e)
    if a < b:
        gaps.append([a, b])
    return gaps


def follow_all(source, project, candidates, frames=(), scanned=(), progress=None, words=()):
    """({candidate id: scenes or None}, frames, scanned spans) for every candidate.

    Each stretch of the source is scanned once: overlapping clips share frames, and
    `frames`/`scanned` from an earlier run are reused, so only new stretches cost time.
    Voices are learned from everything said in the source, and where each one sits across all clips;
    a clip whose two people those voices don't tell apart learns its own (clip_voices).
    """
    frames, scanned = list(frames), [list(s) for s in scanned]
    gaps = []
    for c in candidates:
        for gap in uncovered([c['ranges'][0][0], c['ranges'][-1][1]], scanned + gaps):
            gaps.append(gap)
    total, done = max(1, sum(b - a for a, b in gaps)), 0
    for a, b in gaps:
        frames += detect_frames(source, a, b, project['width'], project['height'],
                                progress and (lambda p, a=a, b=b: progress(min(99, round((done + p / 100 * (b - a)) * 100 / total)))))
        scanned.append([a, b])
        done += b - a
    frames.sort(key=lambda f: f[0])
    lines = [(s['start_ms'], s['end_ms']) for s in project['transcript'] if s['end_ms'] - s['start_ms'] >= 100]
    pieces, pitches, timbres = source_speech(source, lines, list(words))
    weights = [e - s for s, e in pieces]
    clips = []
    for c in candidates:
        a, b = c['ranges'][0][0], c['ranges'][-1][1]
        try:
            edges = panel_edges(source, a, b)
        except Exception:
            edges = []  # crops may cross a composite seam, but still follow the speaker
        clips.append((c, track_faces([f for f in frames if a <= f[0] < b]), within(pieces, a, b), edges))
    labels = voice_labels(pitches, weights, timbres)
    seats = seat_positions([seat for _, tracks, inside, edges in clips
                            for seat in voice_seats(tracks, [p for _, p in inside], [labels[k] for k, _ in inside], edges)])
    if len(seats) == 2:
        # Now that each voice's seat is known, mouths help settle the pieces whose voice is in between.
        mouths = [None] * len(pieces)
        for _, tracks, inside, edges in clips:
            for (k, _), mouth in zip(inside, seat_mouths(tracks, [p for _, p in inside], seats, edges)):
                mouths[k] = mouth if mouths[k] is None else mouths[k]
        labels = voice_labels(pitches, weights, timbres, mouths)
    result = {}
    for c, tracks, inside, edges in clips:
        lines, voices = [p for _, p in inside], [labels[k] for k, _ in inside]
        own = seats
        # The source's voices put (nearly) all of this clip in one voice and tie neither to a face here,
        # so they don't tell its two people apart: learn them from the clip.
        if (min(voices.count(0), voices.count(1)) < 2 and len(seat_positions(voice_seats(tracks, lines, voices, edges))) < 2
                and (found := clip_voices(tracks, lines, [timbres[k] for k, _ in inside], [pitches[k] for k, _ in inside], edges))):
            voices, own = found
        result[c['id']] = _clip_scenes(project, c, tracks, speaker_turns(tracks, lines, voices, own, edges), edges)
    return result, frames, scanned


def _clip_scenes(project, c, tracks, turns, edges=()):
    """New scenes for candidate c, or None when no speaking face is found."""
    if not turns:
        return None
    w, h = project['width'], project['height']
    first = c['scenes'][0]
    if any(s['layout'] == 'debate' for s in c['scenes']):
        # A clip in the Debate layout keeps it: the close-up below follows the speaker.
        return follow_scenes(tracks, turns, None, c['ranges'][-1][1], (debate_top(tracks, w, h), w, h), edges)
    if first['layout'] == 'fill':
        size = first['crops'][0][2:]
    else:
        aspect = 9 / 16 if project['aspect_ratio'] == '9:16' else 16 / 9
        size = [min(1, h * aspect / w), min(1, w / aspect / h)]
    return follow_scenes(tracks, turns, size, c['ranges'][-1][1], edges=edges)
