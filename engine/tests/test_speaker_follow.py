"""Automatic full-frame speaker following uses the editor's voice and face detection."""
from clip_engine.services import editor_speaker, speaker_follow
from clip_engine.services.layout_analyzer import Box, ClipLayoutPlan, LayoutType, ShotLayout
from clip_engine.services.speaker_follow import follow_speakers, label_voices
from clip_engine.services.transcription_service import TranscriptSegment


def test_two_speaker_labels_are_the_voices_otherwise_pitch_decides():
    assert label_voices(['S2', 'S1', None, 'S2']) == [1, 0, None, 1]
    assert label_voices([None] * 4) is None
    assert label_voices(['S1', 'S2', 'S3', 'S1']) is None


def test_a_two_person_shot_cuts_to_whoever_speaks_even_with_a_hidden_mouth(monkeypatch):
    # Host left talks 0-4s and 8-12s; the guest's mouth (right) is behind a mic and never registers.
    talker = lambda t: 1 if 4000 <= t < 8000 else 0
    frames = [(t, [(.2, .4, .08, .14, 5.0 if talker(t) == 0 else .2), (.8, .4, .07, .12, .1)]) for t in range(0, 12000, 125)]
    monkeypatch.setattr(editor_speaker, 'detect_frames', lambda source, a, b, w, h, progress=None: [(t + a, f) for t, f in frames])
    monkeypatch.setattr(editor_speaker, 'read_audio', lambda source, a, b: None)
    monkeypatch.setattr(editor_speaker, 'piece_pitch', lambda audio, a, pieces: [120 if talker(s - a) == 0 else 260 for s, _ in pieces])
    shot = ShotLayout(0, 12000, LayoutType.TALKING_HEAD, people=[Box(.16, .33, .08, .14)])
    plan = ClipLayoutPlan([shot], 1920, 1080, face_samples=[(t, [Box(.16, .33, .08, .14), Box(.76, .34, .07, .12)]) for t in range(0, 12000, 500)])
    segments = [TranscriptSegment(start_time_ms=s + 50000, end_time_ms=e + 50000, text='x') for s, e in [(0, 2000), (2000, 4000), (4000, 6000), (6000, 8000), (8000, 12000)]]
    shots = follow_speakers(plan, 'src', 50000, segments).shots
    assert [(s.start_ms, s.end_ms, round(s.people[0].cx, 1)) for s in shots] == [(0, 4000, .2), (4000, 8000, .8), (8000, 12000, .2)]
    assert all(s.source == 'speaker' and s.layout == LayoutType.TALKING_HEAD for s in shots)


def test_a_lone_talking_head_is_not_scanned(monkeypatch):
    monkeypatch.setattr(speaker_follow.es, 'detect_frames', lambda *a, **k: (_ for _ in ()).throw(AssertionError('scanned')))
    shot = ShotLayout(0, 5000, LayoutType.TALKING_HEAD)
    plan = ClipLayoutPlan([shot], 1920, 1080, face_samples=[(t, [Box(.4, .3, .1, .15)]) for t in range(0, 5000, 500)])
    assert follow_speakers(plan, 'src', 0, [TranscriptSegment(start_time_ms=0, end_time_ms=5000, text='x')]) is plan


def test_debate_puts_both_people_on_top_and_the_speaker_below(monkeypatch):
    from clip_engine.services.layout_renderer import caption_anchor, shot_views, title_y
    from clip_engine.services.manual_editor import validate_candidate
    talker = lambda t: 1 if 4000 <= t < 8000 else 0
    frames = [(t, [(.2, .4, .08, .14, 5.0 if talker(t) == 0 else .2), (.8, .4, .07, .12, 5.0 if talker(t) == 1 else .2)])
              for t in range(0, 12000, 125)]
    monkeypatch.setattr(editor_speaker, 'detect_frames', lambda source, a, b, w, h, progress=None: [(t + a, f) for t, f in frames])
    monkeypatch.setattr(editor_speaker, 'read_audio', lambda source, a, b: None)
    monkeypatch.setattr(editor_speaker, 'piece_pitch', lambda audio, a, pieces: [120 if talker(s - a) == 0 else 260 for s, _ in pieces])
    shot = ShotLayout(0, 12000, LayoutType.TWO_SHOT, people=[Box(.16, .33, .08, .14), Box(.76, .34, .07, .12)])
    plan = ClipLayoutPlan([shot], 1920, 1080, face_samples=[(t, [Box(.16, .33, .08, .14), Box(.76, .34, .07, .12)]) for t in range(0, 12000, 500)])
    segments = [TranscriptSegment(start_time_ms=s, end_time_ms=e, text='x') for s, e in [(0, 4000), (4000, 8000), (8000, 12000)]]
    ((_, scenes),) = speaker_follow.debate_scenes(plan, 'src', 0, segments)
    assert [(s['at_ms'], s['layout']) for s in scenes] == [(0, 'debate'), (4000, 'debate'), (8000, 'debate')]
    top, below = scenes[1]['crops']
    assert top[0] < .2 and top[0] + top[2] > .8  # both people stay in view on top
    assert below[0] < .8 < below[0] + below[2] and below[2] < .4  # a close-up on the right-hand speaker
    validate_candidate({'title': 'x', 'ranges': [[0, 12000]], 'scenes': scenes, 'video_speed': 1, 'captions': True}, 12000)
    shots = speaker_follow.debate_speakers(plan, 'src', 0, segments).shots
    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 4000), (4000, 8000), (8000, 12000)]
    (_, (_, top_y, _, top_h)), (_, (_, low_y, _, low_h)) = shot_views(shots[1], 6000, 1920, 1080, 1080, 1920)
    assert (top_y, top_h, low_y, low_h) == (0, 672, 672, 1248)
    assert caption_anchor(shots[1], 1920, 1080, 1080, 1920) == (5, 672)  # captions sit on the seam
    assert title_y(shots[1], 1920, 1080, 1080, 1920, 111) == 672 - 90 - 111  # the title sits just above them


def test_debate_follows_two_speakers_with_a_bystander_the_analyzer_called_a_group(monkeypatch):
    # Three steady faces make the analyzer pick a whole-frame SCREEN shot; Debate still applies.
    talker = lambda t: 1 if 4000 <= t < 8000 else 0
    frames = [(t, [(.3, .4, .08, .14, 5.0 if talker(t) == 0 else .2), (.8, .4, .07, .12, 5.0 if talker(t) == 1 else .2),
                   (.12, .4, .06, .11, .1)]) for t in range(0, 12000, 125)]
    monkeypatch.setattr(editor_speaker, 'detect_frames', lambda source, a, b, w, h, progress=None: [(t + a, f) for t, f in frames])
    monkeypatch.setattr(editor_speaker, 'read_audio', lambda source, a, b: None)
    monkeypatch.setattr(editor_speaker, 'piece_pitch', lambda audio, a, pieces: [120 if talker(s - a) == 0 else 260 for s, _ in pieces])
    plan = ClipLayoutPlan([ShotLayout(0, 12000, LayoutType.SCREEN)], 1920, 1080,
                          face_samples=[(t, [Box(.26, .33, .08, .14), Box(.76, .34, .07, .12), Box(.09, .35, .06, .11)]) for t in range(0, 12000, 500)])
    segments = [TranscriptSegment(start_time_ms=s, end_time_ms=e, text='x') for s, e in [(0, 4000), (4000, 8000), (8000, 12000)]]
    shots = speaker_follow.debate_speakers(plan, 'src', 0, segments).shots
    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 4000), (4000, 8000), (8000, 12000)]
    assert all(len(s.manual_crops) == 2 for s in shots)
    # An inset (letterboxed or pillarboxed video) keeps the analyzer's whole-frame framing.
    inset = ClipLayoutPlan([ShotLayout(0, 12000, LayoutType.SCREEN, content_box=Box(.1, .1, .8, .8))], 1920, 1080, face_samples=plan.face_samples)
    assert speaker_follow.debate_speakers(inset, 'src', 0, segments) is inset


def test_debate_frames_a_shot_without_two_people_as_a_full_frame_close_up(monkeypatch):
    frames = [(t, [(.5, .4, .1, .15, 5.0)]) for t in range(0, 6000, 125)]
    monkeypatch.setattr(editor_speaker, 'detect_frames', lambda source, a, b, w, h, progress=None: frames)
    monkeypatch.setattr(editor_speaker, 'read_audio', lambda source, a, b: None)
    monkeypatch.setattr(editor_speaker, 'piece_pitch', lambda audio, a, pieces: [None] * len(pieces))
    shot = ShotLayout(0, 6000, LayoutType.TALKING_HEAD)
    # A passer-by in one sample gets the scan going; the shot still has one person talking.
    plan = ClipLayoutPlan([shot], 1920, 1080, face_samples=[(0, [Box(.45, .3, .1, .15), Box(.9, .3, .03, .05)])])
    (shot,) = speaker_follow.debate_speakers(plan, 'src', 0, [TranscriptSegment(start_time_ms=0, end_time_ms=6000, text='x')]).shots
    assert not shot.manual_top_share and len(shot.manual_crops) == 1  # no Debate panel, one close-up
    crop = shot.manual_crops[0]
    assert crop[0] < .5 < crop[0] + crop[2] and crop[2] < 1


def test_debate_style_crops_wide_shots_instead_of_blurring_them():
    from clip_engine.config import LayoutStyle
    from clip_engine.services.layout_analyzer import apply_style
    shot = apply_style(ShotLayout(0, 6000, LayoutType.SCREEN, people=[Box(.4, .3, .1, .15)]), LayoutStyle.DEBATE, 1920, 1080)
    assert shot.layout == LayoutType.TALKING_HEAD and shot.focus_path == [(0, .45, .375)]


def test_voices_are_learned_from_the_whole_conversation(monkeypatch):
    # In this clip the host speaks once; elsewhere in the source he speaks often.
    talker = lambda t: 0 if t < 20000 or 24000 <= t < 26000 else 1
    frames = [(t, [(.2, .4, .08, .14, 5.0 if talker(t + 20000) == 0 else .2), (.8, .4, .07, .12, .2)]) for t in range(0, 10000, 125)]
    monkeypatch.setattr(editor_speaker, 'detect_frames', lambda source, a, b, w, h, progress=None: [(t + a, f) for t, f in frames])
    monkeypatch.setattr(editor_speaker, 'read_audio', lambda source, a, b: None)
    monkeypatch.setattr(editor_speaker, 'piece_pitch', lambda audio, a, pieces: [120 if talker(s) == 0 else 260 for s, _ in pieces])
    shot = ShotLayout(0, 10000, LayoutType.TALKING_HEAD)
    plan = ClipLayoutPlan([shot], 1920, 1080, face_samples=[(t, [Box(.16, .33, .08, .14), Box(.76, .34, .07, .12)]) for t in range(0, 10000, 500)])
    everything = [TranscriptSegment(start_time_ms=s, end_time_ms=s + 2000, text='x') for s in range(0, 30000, 2000)]
    shots = follow_speakers(plan, 'src', 20000, everything).shots
    assert [(s.start_ms, round(s.people[0].cx, 1)) for s in shots] == [(0, .8), (4000, .2), (6000, .8)]
