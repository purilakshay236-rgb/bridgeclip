"""Shareable clip filenames.

Every run used to save clip_00.mp4, clip_01.mp4, ... so sending clips from two
runs to a phone collided ("already has a file named clip_00.mp4"). A name
carries the save date and time plus a word from the video title instead,
e.g. "24-10-26 2212 americans clip 01".
"""
import re
from datetime import datetime

# Leading title words that say nothing about the video.
_SKIP = {'the', 'and', 'for', 'are', 'was', 'were', 'you', 'your', 'how', 'why', 'what', 'who', 'when',
         'where', 'this', 'that', 'with', 'from', 'should', 'would', 'could', 'can', 'does', 'did', 'has',
         'have', 'his', 'her', 'its', 'our', 'their', 'they', 'about', 'into', 'today', 'just', 'not'}


def clip_stem(title: str, index: int, when: datetime) -> str:
    """Filename without extension; sidecars (.srt, .framing.json) share it."""
    words = [w for w in re.findall(r'[a-z]+', title.lower()) if len(w) > 2 and w not in _SKIP]
    return f"{when:%d-%m-%y %H%M} {words[0][:20] if words else 'video'} clip {index:02d}"
