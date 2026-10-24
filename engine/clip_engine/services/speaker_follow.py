"""Speaker following for automatic renders: Full frame cuts a talking-head shot to whoever is talking;
Debate shows both people on top and the current speaker large below.

Uses the editor's Follow speaker detection (editor_speaker), so both frame the same person:
faces are tracked with YuNet, speech is split into pieces at pauses, each piece is given to a
voice by the transcript's speaker labels when it names exactly two speakers, else by pitch and
timbre learned from the whole conversation, and each voice sits with the face whose mouth moves
during it, or the other main face when its mouth is hidden (a handheld mic). Without two voices,
mouth movement alone decides.
"""

import logging
from dataclasses import replace
from statistics import median

from clip_engine.services import editor_speaker as es
from clip_engine.services.layout_analyzer import (Box, ClipLayoutPlan, LayoutType, ShotLayout, inset_crop_width,
                                                  smooth_focus_path)

logger = logging.getLogger(__name__)


def label_voices(labels: list[str | None]) -> list[int | None] | None:
    """0, 1 or None per piece from the transcript's speaker labels when they name exactly two speakers, else None."""
    names = sorted({label for label in labels if label})
    return [names.index(label) if label else None for label in labels] if len(names) == 2 else None


def speaker_label(segments: list, start_ms: int, end_ms: int) -> str | None:
    """The speaker label of the transcript segment overlapping [start_ms, end_ms) most."""
    best = max(segments, key=lambda seg: min(end_ms, seg.end_time_ms) - max(start_ms, seg.start_time_ms), default=None)
    return getattr(best, 'speaker_label', None) if best and min(end_ms, best.end_time_ms) > max(start_ms, best.start_time_ms) else None


def shot_turns(tracks: list, turns: list[tuple[int, int]], start_ms: int, end_ms: int) -> list[tuple[int, int, int]]:
    """(start, end, track) per turn covering [start_ms, end_ms); a turn on the same face extends the last."""
    result: list[tuple[int, int, int]] = []
    for j, (start, i) in enumerate(turns):
        stop = turns[j + 1][0] if j + 1 < len(turns) else end_ms
        if result and result[-1][2] == i:
            result[-1] = (result[-1][0], stop, i)
        else:
            result.append((start_ms if not result else start, stop, i))
    return result


def turn_shot(shot: ShotLayout, track: list, start: int, stop: int, window_start_ms: int, src_w: int, src_h: int,
              edges: list[tuple[int, list[float]]]) -> ShotLayout:
    """The shot cut to [start, stop) (source ms), its crop following the speaker's face as they move
    and staying inside the composite panel (es.panel_edges) that holds them."""
    boxes = [(t - start, Box(cx - w / 2, cy - h / 2, w, h)) for t, cx, cy, w, h, _ in track if start <= t < stop]
    if not boxes:  # off screen for this turn (a cutaway): where they usually are
        cx, cy, w, h = (median(s[k] for s in track) for k in (1, 2, 3, 4))
        boxes = [(0, Box(cx - w / 2, cy - h / 2, w, h))]
    person = boxes[len(boxes) // 2][1]
    bounds = shot.crop_bounds
    seams = es.edges_at(edges, (start + stop) / 2)
    if bounds is None and seams:
        lo = max((e for e in seams if e <= person.cx), default=0)
        hi = min((e for e in seams if e > person.cx), default=1)
        if inset_crop_width(None, src_w, src_h) <= hi - lo:
            bounds = Box(lo, 0, hi - lo, 1)
    return replace(shot, start_ms=start - window_start_ms, end_ms=stop - window_start_ms, people=[person], crop_bounds=bounds,
                   focus_path=smooth_focus_path(boxes, stop - start, inset_crop_width(bounds, src_w, src_h)), source="speaker")


def shot_speakers(plan: ClipLayoutPlan, video_path: str, window_start_ms: int, transcript_segments: list,
                  layouts: tuple[str, ...]) -> tuple[list[tuple[ShotLayout, list, list[tuple[int, int]]]], list]:
    """(shot, face tracks, turns [(start, track)]) per shot, no turns where nobody is followed; and the
    window's composite seams (es.panel_edges).

    Only shots in `layouts` are followed, and nothing is scanned unless one of them shows two faces at once.
    """
    unfollowed = [(shot, [], []) for shot in plan.shots], []
    a = window_start_ms
    b = a + max((shot.end_ms for shot in plan.shots), default=0)
    # A lone talking head needs no scan: the analyzer's face samples must show two faces at once.
    candidates = [shot for shot in plan.shots if shot.layout in layouts]
    if not any(len(faces) >= 2 for t, faces in plan.face_samples for shot in candidates if shot.start_ms <= t < shot.end_ms):
        return unfollowed
    lines = [(seg.start_time_ms, seg.end_time_ms) for seg in transcript_segments if seg.end_time_ms - seg.start_time_ms >= 100]
    words = [(w.start_time_ms, w.end_time_ms) for seg in transcript_segments for w in getattr(seg, 'words', None) or []]
    try:
        all_pieces, pitches, timbres = es.source_speech(video_path, lines, words)
        inside = es.within(all_pieces, a, b)
        if not inside:
            return unfollowed
        frames = es.detect_frames(video_path, a, b, plan.source_width, plan.source_height)
    except Exception as e:
        logger.warning(f"Speaker following unavailable; keeping the analyzer's framing: {e}")
        return unfollowed
    try:
        edges = es.panel_edges(video_path, a, b)
    except Exception as e:
        logger.warning(f"Composite panel scan failed; crops may cross a seam: {e}")
        edges = []
    pieces = [p for _, p in inside]
    tracks = es.track_faces(frames)
    voices = label_voices([speaker_label(transcript_segments, s, e) for s, e in pieces])
    if voices is None:
        # Voices learned from the whole conversation, then whose mouth moves for the pieces in between.
        weights = [e - s for s, e in all_pieces]
        labels = es.voice_labels(pitches, weights, timbres)
        voices = [labels[k] for k, _ in inside]
        seats = es.seat_positions(es.voice_seats(tracks, pieces, voices, edges))
        if len(seats) == 2:
            mouths = [None] * len(all_pieces)
            for (k, _), mouth in zip(inside, es.seat_mouths(tracks, pieces, seats, edges)):
                mouths[k] = mouth
            labels = es.voice_labels(pitches, weights, timbres, mouths)
            voices = [labels[k] for k, _ in inside]
    seats = es.seat_positions(es.voice_seats(tracks, pieces, voices, edges))
    # The source's voices don't tell this clip's two people apart (a compilation's host and a guest of the
    # same voice range): learn them from the clip.
    if len(seats) < 2 and (found := es.clip_voices(tracks, pieces, [timbres[k] for k, _ in inside],
                                                   [pitches[k] for k, _ in inside], edges)):
        voices, seats = found
    result = []
    for shot in plan.shots:
        s, e = a + shot.start_ms, a + shot.end_ms
        inside = [(k, (max(s, p0), min(e, p1))) for k, (p0, p1) in enumerate(pieces) if p1 > s and p0 < e]
        if shot.layout not in layouts or not inside:
            result.append((shot, [], []))
            continue
        tracks = es.track_faces([f for f in frames if s <= f[0] < e])
        result.append((shot, tracks, es.speaker_turns(tracks, [p for _, p in inside], [voices[k] for k, _ in inside], seats, edges)))
    return result, edges


def follow_speakers(plan: ClipLayoutPlan, video_path: str, window_start_ms: int,
                    transcript_segments: list) -> ClipLayoutPlan:
    """Split talking-head shots with two or more people into close-ups of the current speaker."""
    a = window_start_ms
    shots: list[ShotLayout] = []
    followed, edges = shot_speakers(plan, video_path, a, transcript_segments, (LayoutType.TALKING_HEAD,))
    for shot, tracks, turns in followed:
        turns = shot_turns(tracks, turns, a + shot.start_ms, a + shot.end_ms) if turns else []
        if not turns:
            shots.append(shot)
            continue
        shots.extend(turn_shot(shot, tracks[i], start, stop, a, plan.source_width, plan.source_height, edges)
                     for start, stop, i in turns)
    if shots == plan.shots:
        return plan
    logger.info(f"Speaker following: {len(plan.shots)} shot(s) -> {len(shots)}")
    return replace(plan, shots=shots)


def two_people(tracks: list, start_ms: int, end_ms: int) -> bool:
    """Whether two similar-sized people stay apart on screen through most of [start_ms, end_ms)."""
    span = max(1, (end_ms - start_ms) * es.FPS / 1000)
    steady = [(es._cx(t), median(s[3] for s in t)) for t in tracks if len(t) >= .5 * span]
    if len(steady) < 2:
        return False
    largest = max(w for _, w in steady)
    seats = [x for x, w in steady if w >= .6 * largest]
    return max(seats) - min(seats) >= es.SEAT_RADIUS


def speaker_scenes(plan: ClipLayoutPlan, video_path: str, window_start_ms: int, transcript_segments: list,
                   layout: str = 'fill') -> list[tuple[ShotLayout, list[dict] | None]]:
    """Per shot, editor scenes (source ms, the first at the shot's start) that follow whoever speaks,
    gliding to keep their face centred: a Full frame crop ('fill', talking-head shots), or the Debate
    layout ('debate': both people on top, the speaker below, or Full frame where two people aren't on screen).
    None for shots left to the analyzer."""
    a, w, h = window_start_ms, plan.source_width, plan.source_height
    debate = layout == 'debate'
    # The analyzer calls three or more faces a whole-frame group (SCREEN), which a two-person
    # conversation with a bystander in view is; Debate still needs two_people below.
    layouts = (LayoutType.TALKING_HEAD, LayoutType.TWO_SHOT, LayoutType.SCREEN) if debate else (LayoutType.TALKING_HEAD,)
    size = (min(1, h * 9 / 16 / w), min(1, w * 16 / 9 / h))  # the 9:16 Full frame crop
    result: list[tuple[ShotLayout, list[dict] | None]] = []
    followed, edges = shot_speakers(plan, video_path, a, transcript_segments, layouts)
    for shot, tracks, turns in followed:
        s, e = a + shot.start_ms, a + shot.end_ms
        if debate and not turns and tracks:  # nobody placed as speaking: keep the main face in view
            turns = [(s, max(range(len(tracks)), key=lambda i: len(tracks[i]) * median(p[3] for p in tracks[i])))]
        # A padded inset keeps the analyzer's crop inside it.
        if not turns or shot.crop_bounds is not None or shot.content_box is not None:
            result.append((shot, None))
            continue
        # Debate needs two people on screen; otherwise the speaker gets a Full frame close-up.
        top = (es.debate_top(tracks, w, h), w, h) if debate and two_people(tracks, s, e) else None
        scenes = es.follow_scenes(tracks, turns, size, e, top, edges)
        scenes[0]['at_ms'] = s
        result.append((shot, scenes))
    return result


def debate_scenes(plan: ClipLayoutPlan, video_path: str, window_start_ms: int,
                  transcript_segments: list) -> list[tuple[ShotLayout, list[dict] | None]]:
    """speaker_scenes in the Debate layout."""
    return speaker_scenes(plan, video_path, window_start_ms, transcript_segments, 'debate')


def debate_speakers(plan: ClipLayoutPlan, video_path: str, window_start_ms: int,
                    transcript_segments: list) -> ClipLayoutPlan:
    """Render two-person shots in the Debate layout and other shots with a speaker as Full frame close-ups
    of them; shots nobody is followed in keep the analyzer's framing."""
    from clip_engine.services.manual_editor import manual_plan
    shots: list[ShotLayout] = []
    for shot, scenes in debate_scenes(plan, video_path, window_start_ms, transcript_segments):
        if scenes is None:
            shots.append(shot)
            continue
        s, e = window_start_ms + shot.start_ms, window_start_ms + shot.end_ms
        part = manual_plan({'width': plan.source_width, 'height': plan.source_height},
                           {'ranges': [[s, e]], 'scenes': [{**scenes[0], 'at_ms': 0}, *scenes[1:]]})
        shots.extend(replace(p, start_ms=p.start_ms + shot.start_ms, end_ms=p.end_ms + shot.start_ms,
                             detected_layout=shot.detected_layout or shot.layout, source="speaker") for p in part.shots)
    if shots == plan.shots:
        return plan
    logger.info(f"Debate layout: {len(plan.shots)} shot(s) -> {len(shots)}")
    return replace(plan, shots=shots)
