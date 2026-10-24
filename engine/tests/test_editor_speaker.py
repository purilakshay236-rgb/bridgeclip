"""Follow speaker frames whoever's mouth moves during each transcript line."""
from clip_engine.services.manual_editor import validate_candidate
from clip_engine.services.editor_speaker import AUDIO_RATE, clip_voices, follow_scenes, piece_pitch, speaker_turns, speech_pieces, track_faces, voice_labels


def frames(talker_by_time):
    """Two faces (left, right) at 8 fps; the talker's mouth moves."""
    result = []
    for t in range(0, 10000, 125):
        talker = talker_by_time(t)
        result.append((t, [(.2, .4, .08, .14, 6.0 if talker == 0 else .2),
                           (.75, .45, .07, .12, 6.0 if talker == 1 else .2)]))
    return result


def test_each_line_frames_its_speaker_and_short_interjections_keep_the_frame():
    tracks = track_faces(frames(lambda t: 0 if t < 4000 or 5400 <= t < 5900 else 1))
    assert len(tracks) == 2
    lines = [(0, 2000), (2000, 4000), (4000, 5400), (5400, 5900), (5900, 10000)]
    turns = speaker_turns(tracks, lines)
    assert [start for start, _ in turns] == [0, 4000]
    left, right = (turns[0][1], turns[1][1])
    assert tracks[left][0][1] < .5 < tracks[right][0][1]
    scenes = follow_scenes(tracks, turns, [.316, 1], 10000)
    assert [s['at_ms'] for s in scenes] == [0, 4000]
    assert abs(scenes[0]['crops'][0][0] - (.2 - .158)) < 1e-6
    assert abs(scenes[1]['crops'][0][0] - (.75 - .158)) < 1e-6
    validate_candidate({'title': 'x', 'ranges': [[0, 10000]], 'scenes': scenes, 'video_speed': 1, 'captions': True}, 10000)


def test_no_movement_or_no_faces_finds_no_speaker():
    still = [(t, [(.5, .4, .1, .15, .1)]) for t in range(0, 4000, 125)]
    assert speaker_turns(track_faces(still), [(0, 4000)]) == []
    assert speaker_turns([], [(0, 4000)]) == []


def test_crops_stay_inside_the_frame_and_turns_are_capped():
    tracks = track_faces(frames(lambda t: (t // 125) % 2))
    lines = [(t, t + 125) for t in range(0, 10000, 125)]
    turns = speaker_turns(tracks, lines)
    assert len(turns) <= 60
    edge = [[(0, .99, .5, .1, .1, 5.0)]]
    (scene,) = follow_scenes(edge, [(0, 0)], [.316, .5], 1000)
    x, y, w, h = scene['crops'][0]
    assert x + w <= 1 and y + h <= 1 and x >= 0 and y >= 0


def test_follow_all_scans_each_stretch_once_and_reuses_earlier_scans(monkeypatch):
    from clip_engine.services import editor_speaker
    calls = []

    def fake_detect(source, a, b, w, h, progress=None):
        calls.append((a, b))
        return [f for f in frames(lambda t: 0 if t < 4000 else 1) if a <= f[0] < b]
    monkeypatch.setattr(editor_speaker, 'detect_frames', fake_detect)
    monkeypatch.setattr(editor_speaker, 'read_audio', lambda source, a, b: None)
    project = {'width': 1920, 'height': 1080, 'aspect_ratio': '9:16',
               'transcript': [{'start_ms': 0, 'end_ms': 4000}, {'start_ms': 4000, 'end_ms': 10000}]}
    clip = lambda id, a, b: {'id': id, 'ranges': [[a, b]], 'scenes': [{'at_ms': 0, 'layout': 'fill', 'crops': [[0, 0, .316, 1]]}]}
    found, cached, scanned = editor_speaker.follow_all('src', project, [clip('a', 0, 6000), clip('b', 3000, 10000)])
    assert calls == [(0, 6000), (6000, 10000)]
    assert [s['at_ms'] for s in found['b']] == [0, 4000]
    calls.clear()
    found, _, _ = editor_speaker.follow_all('src', project, [clip('b', 3000, 10000)], cached, scanned)
    assert calls == [] and [s['at_ms'] for s in found['b']] == [0, 4000]


def test_long_lines_split_at_pauses_and_a_quiet_face_cannot_steal_a_talking_speaker():
    assert speech_pieces([(0, 10000)], [(0, 900), (1000, 2000), (2600, 4000), (4100, 8000), (8100, 9500)]) == [(0, 2000), (2600, 4000), (4100, 8000), (8100, 9500)]
    # Man's mouth barely registers but stands out against his own still level; a head turn while the woman talks doesn't switch.
    talk = lambda t: [(.2, .4, .08, .14, (.9 if t >= 6000 else .45 if 3000 <= t < 4500 else .0)),
                      (.75, .45, .08, .14, (.1 if t >= 6000 else 2.5 if 3000 <= t < 4500 else 4.0))]
    tracks = track_faces([(t, talk(t)) for t in range(0, 9000, 125)])
    turns = speaker_turns(tracks, [(0, 3000), (3000, 4500), (4500, 6000), (6000, 9000)])
    assert [(s, tracks[i][0][1] < .5) for s, i in turns] == [(0, False), (6000, True)]


def test_voices_pick_the_face_even_when_a_mic_hides_one_mouth():
    # Right face talks in voice 0; the left face's mouth is hidden and reads noise.
    lines = [(0, 3000), (3000, 6000), (6000, 9000), (9000, 12000)]
    voices = [1, 0, 1, 0]
    act = lambda t: [(.2, .4, .08, .14, .5 if (t // 1500) % 2 else .3), (.75, .45, .08, .14, 5.0 if (t // 3000) % 2 else .2)]
    tracks = track_faces([(t, act(t)) for t in range(0, 12000, 125)])
    turns = speaker_turns(tracks, lines, voices)
    assert [(s, tracks[i][0][1] < .5) for s, i in turns] == [(0, True), (3000, False), (6000, True), (9000, False)]


def test_tracks_rejoin_after_a_cutaway():
    face = [(t, [(.2, .4, .08, .14, 1.0)]) for t in range(0, 3000, 125)]
    closeup = [(t, [(.7, .4, .2, .3, 1.0)]) for t in range(3000, 5000, 125)]
    back = [(t, [(.21, .4, .08, .14, 1.0)]) for t in range(5000, 8000, 125)]
    assert [len(t) for t in track_faces(face + closeup + back)] == [48, 16]


def test_pitch_splits_two_voices_but_not_one_persons_intonation():
    import numpy as np
    rng = np.random.default_rng(0)

    def voice(f0, ms):
        t = np.arange(int(ms * AUDIO_RATE / 1000)) / AUDIO_RATE
        return sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8)) * .1 + rng.normal(0, .005, len(t))
    parts = [(120, 2000), (260, 2000), (150, 2500), (300, 1500)]
    audio = np.concatenate([voice(f, ms) for f, ms in parts]).astype(np.float32)
    starts = np.cumsum([0] + [ms for _, ms in parts])
    pitches = piece_pitch(audio, 0, [(int(a), int(b)) for a, b in zip(starts, starts[1:])])
    assert all(abs(p - f) / f < .05 for p, (f, _) in zip(pitches, parts))
    w = [1] * 6
    assert voice_labels([120, 260, 150, 300, 140, 280], w) == [0, 1, 0, 1, 0, 1]
    assert voice_labels([130, 170, 150, 190, 140, 180], w) == [None] * 6  # one person's range
    assert voice_labels([120, 260, 150, 300, 200, 280], w)[4] is None  # in between: keep the framing


def test_a_voice_keeps_its_seat_when_its_mouth_cannot_be_read():
    # The left face's mouth never registers (handheld mic); seats learned in other clips still frame it.
    act = lambda t: [(.2, .4, .08, .14, .0), (.75, .45, .08, .14, .0)]
    tracks = track_faces([(t, act(t)) for t in range(0, 9000, 125)])
    lines = [(0, 3000), (3000, 6000), (6000, 9000)]
    turns = speaker_turns(tracks, lines, [0, 1, None], {0: .2, 1: .75})
    assert [(s, tracks[i][0][1] < .5) for s, i in turns] == [(0, True), (3000, False)]


def test_a_crowd_face_beside_the_guest_never_takes_her_seat():
    # The guest's mouth is behind a mic; a smaller face just behind her fidgets during her voice.
    act = lambda t: [(.2, .4, .08, .14, 4.0 if t >= 6000 else .1), (.8, .45, .07, .12, .1), (.72, .3, .05, .08, 3.0 if t < 6000 else .1)]
    tracks = track_faces([(t, act(t)) for t in range(0, 9000, 125)])
    turns = speaker_turns(tracks, [(0, 3000), (3000, 6000), (6000, 9000)], [1, 1, 0], {0: .2, 1: .76})
    assert [(s, round(tracks[i][0][1], 2)) for s, i in turns] == [(0, .8), (6000, .2)]


def test_a_second_guest_of_the_same_voice_takes_her_run_from_the_voices_seat():
    # Two women answer one man. The voice's seat is where the first one stands (.9); after a pause the
    # second (.62), as large, talks while the first fixes her hair (mouth-region movement for one piece).
    def act(t):
        first = 4.0 if 3000 <= t < 6000 or 8000 <= t < 10000 else .1
        second = 3.0 if t >= 8000 else .1
        return [(.2, .4, .08, .14, 4.0 if t < 3000 or t >= 14000 else .1), (.9, .45, .07, .12, first), (.62, .45, .07, .12, second)]
    tracks = track_faces([(t, act(t)) for t in range(0, 16000, 125)])
    lines = [(0, 3000), (3000, 6000), (8000, 10000), (10000, 12000), (12000, 14000), (14000, 16000)]
    turns = speaker_turns(tracks, lines, [0, 1, 1, 1, 1, 0], {0: .2, 1: .9})
    assert [(s, round(tracks[i][0][1], 2)) for s, i in turns] == [(0, .2), (3000, .9), (8000, .62), (14000, .2)]
    # Without the pause it is one run, and the seat's guest keeps it: a run is never split by mouths alone.
    joined = [(0, 3000), (3000, 6000), (6000, 8000), (8000, 10000), (10000, 12000), (12000, 14000), (14000, 16000)]
    turns = speaker_turns(tracks, joined, [0, 1, 1, 1, 1, 1, 0], {0: .2, 1: .9})
    assert [(s, round(tracks[i][0][1], 2)) for s, i in turns] == [(0, .2), (3000, .9), (14000, .2)]


def test_the_first_framing_waits_for_a_clear_voice_when_voices_are_known():
    # The opening piece's pitch is in between; mouth movement must not pick the host for it.
    act = lambda t: [(.2, .4, .08, .14, 5.0 if t < 3000 else .1), (.8, .45, .07, .12, .1)]
    tracks = track_faces([(t, act(t)) for t in range(0, 9000, 125)])
    turns = speaker_turns(tracks, [(0, 3000), (3000, 9000)], [None, 1], {0: .2, 1: .8})
    assert [(s, round(tracks[i][0][1], 2)) for s, i in turns] == [(3000, .8)]
    assert follow_scenes(tracks, turns, [.316, 1], 9000)[0]['at_ms'] == 0


def test_a_voice_that_speaks_rarely_is_still_found_but_a_stray_piece_is_not():
    # The host asks two short questions in a clip the guest dominates.
    assert voice_labels([260, 250, 270, 120, 255, 265, 130, 260], [2000] * 8) == [1, 1, 1, 0, 1, 1, 0, 1]
    # One low reading among one voice (an octave slip) is not a second person.
    assert voice_labels([260, 250, 270, 120, 255, 265, 245, 260], [2000] * 8) == [None] * 8


def test_timbre_decides_where_pitch_is_in_between_and_mouths_break_ties():
    import numpy as np
    rng = np.random.default_rng(1)
    host, guest = np.zeros(26), np.full(26, 3.0)
    t = lambda centre: centre + rng.normal(0, .3, 26)
    # Clear pieces of each voice teach the timbre model.
    clear = [120, 130, 125, 135, 118, 128, 140, 122] + [280, 290, 270, 285, 300, 275, 295, 282]
    taught = [t(host) for _ in range(8)] + [t(guest) for _ in range(8)]
    weights = [2000] * 19
    # A raised male voice at 230 Hz keeps his timbre; the same pitch with her timbre is hers.
    assert voice_labels(clear + [230, 230, 205], weights, taught + [t(host), t(guest), (host + guest) / 2])[16:] == [0, 1, None]
    # A piece whose timbre is right in between is settled only by a clear difference in whose mouth moves.
    assert voice_labels(clear + [230, 230, 205], weights, taught + [t(host), t(guest), (host + guest) / 2], [None] * 18 + [2.0])[18] == 1
    # Decisive pitch with strongly opposite timbre is a tracking slip or two people at once: no call.
    assert voice_labels(clear + [285], weights[:17], taught + [t(host)])[16] is None


def test_a_clip_learns_its_own_voices_when_two_people_share_a_pitch_range_but_a_monologue_does_not():
    import numpy as np
    rng = np.random.default_rng(2)
    host, guest = np.zeros(26), np.full(26, 3.0)
    who = [0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1]
    lines = [(k * 2000, k * 2000 + 1800) for k in range(len(who))]

    def mouths(moving):
        """Two people side by side; per piece, whose mouth moves (None: both, as when the speaker's is hidden)."""
        result = []
        for t in range(0, len(who) * 2000, 125):
            m = moving[t // 2000]
            result.append((t, [(.2, .4, .08, .14, 1.0 if m is None else 5.0 if m == 0 else .2),
                               (.75, .45, .08, .14, 1.0 if m is None else 5.0 if m == 1 else .2)]))
        return track_faces(result)
    # A host and a guest of the same pitch: where mouths are clear they teach timbre, which settles the rest.
    unclear = {3, 8, 13, 17}
    timbres = [(host if v == 0 else guest) + rng.normal(0, .3, 26) for v in who]
    pitches = [120 + rng.normal(0, 8) for _ in who]
    voices, seats = clip_voices(mouths([None if k in unclear else v for k, v in enumerate(who)]), lines, timbres, pitches)
    assert voices == who and [round(x, 2) for x in seats.values()] == [.2, .75]
    # Only the guest talks; the host nods along. One voice has nothing to tell apart: no voices are invented.
    alone = [guest + rng.normal(0, .3, 26) for _ in who]
    assert clip_voices(mouths([0 if k % 3 == 0 else 1 for k in range(len(who))]), lines, alone, pitches) is None


def test_short_pieces_back_to_back_in_one_voice_are_a_turn():
    act = lambda t: [(.2, .4, .08, .14, 1.0), (.75, .45, .08, .14, 1.0)]
    tracks = track_faces([(t, act(t)) for t in range(0, 9000, 125)])
    lines = [(0, 3000), (3000, 3700), (3800, 4500), (4600, 9000)]
    turns = speaker_turns(tracks, lines, [1, 0, 0, 1], {0: .2, 1: .75})
    # Each 0.7 s piece alone would be an interjection; together they are the host's turn.
    assert [(s, round(tracks[i][0][1], 1)) for s, i in turns] == [(0, .8), (3000, .2), (4600, .8)]


def test_the_crop_glides_to_keep_a_moving_speaker_centred():
    # The speaker walks from x=.3 to x=.6 over 8 s.
    track = [(t, .3 + .3 * min(1, t / 8000), .4, .08, .14, 1.0) for t in range(0, 10000, 125)]
    scenes = follow_scenes([track], [(0, 0)], [.316, 1], 10000)
    assert len(scenes) > 3 and all(s.get('transition_ms') for s in scenes[1:])
    for t in range(1000, 10000, 500):
        current = [s for s in scenes if s['at_ms'] <= t - 500][-1]['crops'][0]
        face = .3 + .3 * min(1, t / 8000)
        assert abs(face - (current[0] + current[2] / 2)) <= .2 * current[2]  # never far from the centre
    validate_candidate({'title': 'x', 'ranges': [[0, 10000]], 'scenes': scenes, 'video_speed': 1, 'captions': True}, 10000)


def test_crowd_behind_a_panel_subject_cannot_speak_and_crops_stay_in_their_panel():
    from clip_engine.services.editor_speaker import _piece_stats, keep_in_panel
    seam = [(t, [.5]) for t in range(0, 6000, 1000)]
    # Guest (big, right panel) briefly turns away and measures smaller than a fidgeting face behind her.
    frames = [(t, [(.2, .4, .08, .14, 1.0), (.75, .4, .07 if t >= 500 else .05, .12, .1), (.8, .2, .055, .1, 4.0)])
              for t in range(0, 6000, 125)]
    stats = _piece_stats(track_faces(frames), [(0, 700), (700, 6000)], seam)
    tracks = track_faces(frames)
    for _, _, eligible, *_ in stats:
        assert sorted(round(tracks[i][0][1], 2) for i in eligible) == [.2, .75]
    assert keep_in_panel([.4, 0, .316, 1], .62, [.5]) == [.5, 0, .316, 1]
    assert keep_in_panel([.4, 0, .316, 1], .62, []) == [.4, 0, .316, 1]


def test_composite_seams_are_found_only_while_the_composite_is_on_screen(tmp_path):
    import subprocess
    from clip_engine.services.editor_speaker import panel_edges
    video = tmp_path / 'composite.mp4'
    noise = 'noise=alls=6:allf=t'
    graph = (f'color=c=0x404040:s=320x180:d=4,{noise}[l];color=c=0xc0c0c0:s=320x180:d=4,{noise}[r];[l][r]hstack[split];'
             f'color=c=0x808080:s=640x180:d=3,{noise}[full];[split][full]concat=n=2:v=1[v]')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-filter_complex', graph, '-map', '[v]', '-pix_fmt', 'yuv420p', str(video)], check=True)
    edges = panel_edges(str(video), 0, 7000)
    assert all(abs(x[0] - .5) < .01 for t, x in edges if t < 3800) and len([e for e in edges if e[0] < 3800]) >= 3
    assert all(x == [] for t, x in edges if t > 4300)
