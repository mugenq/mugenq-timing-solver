"""Read-only osu! reference import, ordered onset matching and safe new-map export.

No Qt dependency. All comparison times are in *audio seconds*, and timing-point
rows remain unchanged when deliberately reused. No BPM inference is attempted.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import Path
import csv
import math
import os
import re
import shutil
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class OsuTimingPoint:
    time_s: float
    beat_length_ms: float
    inherited: bool
    line: str

    @property
    def bpm(self) -> float | None:
        return 60000.0 / self.beat_length_ms if self.inherited and self.beat_length_ms > 0 else None


@dataclass(frozen=True)
class OsuReference:
    source_path: str
    audio_filename: str
    metadata: dict[str, str]
    hitobject_times: tuple[float, ...]  # unique circle / slider-head onset times
    timing_points: tuple[OsuTimingPoint, ...]
    raw_hitobject_count: int
    spinner_count: int
    mode: int

    @property
    def red_times(self) -> tuple[float, ...]:
        return tuple(p.time_s for p in self.timing_points if p.inherited)

    @property
    def green_times(self) -> tuple[float, ...]:
        return tuple(p.time_s for p in self.timing_points if not p.inherited)

    @property
    def audio_basename(self) -> str:
        return self.audio_filename.replace('\\', '/').split('/')[-1]

    def matches_audio_name(self, audio_path: str | Path) -> bool:
        return self.audio_basename.casefold() == Path(audio_path).name.casefold()


def _finite_time_ms(value: str, line_number: int, *, allow_negative=False) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'Invalid timestamp at .osu line {line_number}') from exc
    if not math.isfinite(number) or abs(number) > 1e11 or (number < 0 and not allow_negative):
        raise ValueError(f'Out-of-range timestamp at .osu line {line_number}')
    return number / 1000.0


def parse_osu(path: str | Path) -> OsuReference:
    """Parse an existing .osu safely. Does not write or normalize the source file.

    Circle and slider *heads* count as partial onset references. Spinner and
    mania-hold starts do not. Stacked simultaneous heads are counted once.
    """
    source = Path(path)
    if source.suffix.lower() != '.osu':
        raise ValueError('Select a .osu beatmap file')
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Beatmap exceeds the 32 MiB reference import limit')
    text = source.read_text(encoding='utf-8-sig')
    section = ''
    general: dict[str, str] = {}
    metadata: dict[str, str] = {}
    objects: list[float] = []
    timing: list[OsuTimingPoint] = []
    raw_count = 0
    spinner_count = 0
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('//') or line.startswith('osu file format v'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
            continue
        if section in {'General', 'Metadata'}:
            if ':' in line:
                key, value = line.split(':', 1)
                (general if section == 'General' else metadata)[key.strip()] = value.strip()
        elif section == 'TimingPoints':
            fields = [part.strip() for part in line.split(',')]
            if len(fields) < 2:
                raise ValueError(f'Malformed timing point at .osu line {line_number}')
            timestamp = _finite_time_ms(fields[0], line_number, allow_negative=True)
            try:
                beat_length = float(fields[1])
                inherited = int(fields[6]) == 1 if len(fields) >= 7 else beat_length > 0
            except ValueError as exc:
                raise ValueError(f'Malformed timing point at .osu line {line_number}') from exc
            if not math.isfinite(beat_length) or (inherited and beat_length <= 0):
                raise ValueError(f'Invalid beat length at .osu line {line_number}')
            timing.append(OsuTimingPoint(timestamp, beat_length, inherited, line))
        elif section == 'HitObjects':
            fields = line.split(',')
            if len(fields) < 5:
                raise ValueError(f'Malformed hit object at .osu line {line_number}')
            try:
                kind = int(fields[3])
            except ValueError as exc:
                raise ValueError(f'Invalid hit object type at .osu line {line_number}') from exc
            if kind & 3:  # circles and sliders; exclude slider tails and repeats
                objects.append(_finite_time_ms(fields[2], line_number))
                raw_count += 1
            elif kind & 8:
                spinner_count += 1
    if not objects and not timing:
        raise ValueError('No hit objects or timing points were found in this .osu file')
    try:
        mode = int(general.get('Mode', '0'))
    except ValueError as exc:
        raise ValueError('Invalid osu! game mode') from exc
    if mode != 0:
        raise ValueError('Only osu!standard (Mode: 0) reference maps are supported in v0.5')
    return OsuReference(str(source.resolve()), general.get('AudioFilename', ''),
                        metadata, tuple(sorted(set(objects))), tuple(timing),
                        raw_count, spinner_count, mode)


@dataclass(frozen=True)
class Match:
    reference_s: float
    candidate_s: float
    deviation_ms: float
    reference_index: int
    candidate_index: int


@dataclass(frozen=True)
class Comparison:
    matches: tuple[Match, ...]
    missed: tuple[float, ...]
    additional: tuple[float, ...]
    candidate_count: int
    reference_count: int
    tolerance_ms: float
    method: str = 'optimal ordered matching'

    @property
    def recall(self) -> float | None:
        return len(self.matches) / self.reference_count if self.reference_count else None

    @property
    def median_abs_ms(self) -> float | None:
        return median(abs(m.deviation_ms) for m in self.matches) if self.matches else None


def _sorted_nonnegative(values: Iterable[float], label: str) -> list[float]:
    result = [float(value) for value in values]
    if any(not math.isfinite(value) or value < 0 for value in result):
        raise ValueError(f'{label} must contain finite, nonnegative seconds')
    return sorted(result)


def match_onsets(reference_times: Iterable[float], candidate_times: Iterable[float],
                 tolerance_ms: float = 30.0) -> Comparison:
    """One-to-one ordered maximum-cardinality, then minimum-total-error matching.

    Sparse Fenwick dynamic programming visits only time-compatible pairs and
    does not allocate an N×M array for full-length songs. Unmatched detections
    remain *unlabelled*, never presumed false positives.
    """
    refs = _sorted_nonnegative(reference_times, 'Reference times')
    cands = _sorted_nonnegative(candidate_times, 'Candidate times')
    tol = float(tolerance_ms)
    if not math.isfinite(tol) or tol <= 0 or tol > 1000:
        raise ValueError('Matching tolerance must be between 0 and 1000 ms')
    tolerance = tol / 1000.0
    n = len(cands)
    tree: list[tuple[int, float, object] | None] = [None] * (n+1)
    initial = (0, 0.0, None)

    def better(a, b):
        if b is None: return True
        if a[0] != b[0]: return a[0] > b[0]
        return a[1] < b[1] - 1e-10

    def query(j):
        best = initial
        while j:
            if tree[j] is not None and better(tree[j], best):
                best = tree[j]
            j -= j & -j
        return best

    def update(j, state):
        while j <= n:
            if better(state, tree[j]):
                tree[j] = state
            j += j & -j

    for i, t in enumerate(refs):
        start = bisect_left(cands, t - tolerance - 1e-12)
        stop = bisect_right(cands, t + tolerance + 1e-12)
        # Reverse j guarantees no two candidates from the same reference row.
        for j in range(stop - 1, start - 1, -1):
            prev = query(j)
            node = (i, j, prev[2])
            update(j+1, (prev[0]+1, prev[1]+abs(cands[j]-t)*1000, node))
    chain = query(n)[2]
    pairs = []
    while chain is not None:
        i, j, chain = chain
        pairs.append((i, j))
    pairs.reverse()
    used_ref = {i for i, _ in pairs}
    used_cand = {j for _, j in pairs}
    matched = tuple(Match(refs[i], cands[j], (cands[j]-refs[i])*1000, i, j)
                    for i, j in pairs)
    return Comparison(matched,
                      tuple(v for i, v in enumerate(refs) if i not in used_ref),
                      tuple(v for j, v in enumerate(cands) if j not in used_cand),
                      len(cands), len(refs), tol)


def export_comparison_csv(path: str | Path, comparison: Comparison) -> None:
    """CSV with distinct matched/missed/unlabelled rows. No precision estimate."""
    target = Path(path)
    with target.open('x', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['status','reference_seconds','candidate_seconds','signed_error_ms',
                         'tolerance_ms','interpretation'])
        for match in comparison.matches:
            writer.writerow(['matched', f'{match.reference_s:.9f}',f'{match.candidate_s:.9f}',
                             f'{match.deviation_ms:+.3f}', f'{comparison.tolerance_ms:g}',
                             'mapped positive reference'])
        for t in comparison.missed:
            writer.writerow(['unmatched_reference', f'{t:.9f}', '', '',
                             f'{comparison.tolerance_ms:g}', 'no matched candidate'])
        for t in comparison.additional:
            writer.writerow(['unlabelled_detection', '', f'{t:.9f}', '',
                             f'{comparison.tolerance_ms:g}', 'NOT verified false positive'])


@dataclass(frozen=True)
class ExportResult:
    path: str
    exported_circles: int
    rounded_duplicates: int
    timing_mode: str
    timing_points: int
    density_skipped: int = 0
    referenced_audio: str = ''
    audio_copied: bool = False


def _clean_metadata(value: str, default: str) -> str:
    result = re.sub(r'[\r\n\x00-\x1f]', ' ', str(value)).strip()
    return result[:250] if result else default


def export_onset_map(path: str | Path, audio_path: str | Path,
                     current_times: Iterable[float], *,
                     imported: OsuReference | None = None,
                     timing_mode: str = 'placeholder', duration_s: float | None = None,
                     placeholder_bpm: float = 120.0,
                     min_circle_spacing_ms: float = 0.0) -> ExportResult:
    """Create a separate new .osu and ensure referenced audio exists beside it.

    MP3 input is copied as ``audio.mp3`` when needed. Existing audio is never
    overwritten, and high-density thinning affects ONLY the exported map.
    """
    destination = Path(path).resolve()
    audio = Path(audio_path).resolve()
    if destination.suffix.lower() != '.osu':
        raise ValueError('Export filename must end in .osu')
    if not audio.is_file():
        raise FileNotFoundError('Loaded audio no longer exists')
    if audio.suffix.lower() not in ('.mp3', '.ogg', '.wav'):
        raise ValueError('osu! reference export requires MP3, OGG or WAV audio. Convert the FLAC before exporting.')
    if destination == audio or destination.exists():
        raise FileExistsError(f'Refusing to overwrite an existing file: {destination}')
    if timing_mode not in ('placeholder', 'imported'):
        raise ValueError('Invalid timing mode')
    if not math.isfinite(float(placeholder_bpm)) or not 20 <= placeholder_bpm <= 1000:
        raise ValueError('Placeholder BPM must be between 20 and 1000')
    values = _sorted_nonnegative(current_times, 'Current candidates')
    if not values:
        raise ValueError('Current output is empty. Commit candidates before exporting')
    if duration_s is not None and any(t > duration_s + 0.001 for t in values):
        raise ValueError('Current candidates extend past the loaded recording duration')
    ms = sorted({int(math.floor(t*1000 + .5)) for t in values})
    if len(ms) > 500_000:
        raise ValueError('Too many onset circles')
    gap = float(min_circle_spacing_ms)
    if not math.isfinite(gap) or gap < 0 or gap > 1000:
        raise ValueError('Minimum export gap must be between 0 and 1000 ms')
    selected = []
    for time_ms in ms:
        if not selected or time_ms - selected[-1] >= gap:
            selected.append(time_ms)
    skipped = len(ms) - len(selected)
    audio_name = 'audio.mp3' if audio.suffix.lower() == '.mp3' else audio.name
    export_audio = destination.parent / audio_name
    # Never attach a beatmap to an unrelated recording just because its name
    # happens to be audio.mp3. Copy only after every validation succeeds.
    if export_audio.exists():
        if not export_audio.is_file() or export_audio.stat().st_size != audio.stat().st_size:
            raise FileExistsError(f'Existing {export_audio} differs from the loaded audio. Choose another export folder.')
        with audio.open('rb') as original, export_audio.open('rb') as existing:
            while True:
                chunk = original.read(1024 * 1024)
                if chunk != existing.read(1024 * 1024):
                    raise FileExistsError(f'Existing {export_audio} differs from the loaded audio. Choose another export folder.')
                if not chunk:
                    break
    if timing_mode == 'imported':
        if imported is None:
            raise ValueError('Import an osu! reference map to reuse timing')
        if not any(p.inherited for p in imported.timing_points):
            raise ValueError('Imported map contains no uninherited red BPM timing point')
        timing_lines = [point.line for point in imported.timing_points]
    else:
        timing_lines = [f'0,{60000/placeholder_bpm:.12g},4,1,0,60,1,0']
    meta = imported.metadata if imported is not None else {}
    title = _clean_metadata(meta.get('Title', audio.stem), audio.stem)
    artist = _clean_metadata(meta.get('Artist', 'Unknown Artist'), 'Unknown Artist')
    creator = _clean_metadata(meta.get('Creator', 'BeatSolver'), 'BeatSolver')
    tags = _clean_metadata(meta.get('Tags', ''), '')
    lines = [
        'osu file format v14', '', '[General]', f'AudioFilename: {audio_name}',
        'AudioLeadIn: 0', 'PreviewTime: -1', 'Countdown: 0',
        'SampleSet: Normal', 'StackLeniency: 0', 'Mode: 0', '',
        '[Editor]', 'DistanceSpacing: 1', 'BeatDivisor: 4', 'GridSize: 4', 'TimelineZoom: 1', '',
        '[Metadata]', f'Title:{title}',
        f'TitleUnicode:{_clean_metadata(meta.get("TitleUnicode", title),title)}',
        f'Artist:{artist}',
        f'ArtistUnicode:{_clean_metadata(meta.get("ArtistUnicode",artist),artist)}',
        f'Creator:{creator}', f'Version:BeatSolver Onset Reference{(f" Light {gap:g}ms") if gap else ""}',
        f'Tags:{tags} BeatSolver onset reference', 'BeatmapID:0', 'BeatmapSetID:-1', '',
        '[Difficulty]', 'HPDrainRate:5', 'CircleSize:4', 'OverallDifficulty:5',
        f'ApproachRate:{9 if gap else 5}', 'SliderMultiplier:1.4', 'SliderTickRate:1', '',
        '[Events]', '', '[TimingPoints]', *timing_lines, '', '[HitObjects]',
    ]
    for time_ms in selected:
        lines.append(f'256,192,{time_ms},1,0,0:0:0:0:')
    lines.append('')
    # If audio.mp3 isn't already here, create an independent copy exclusively.
    # A failed copy/export must leave neither a truncated audio nor a partial map.
    copied_audio = False
    created_map = False
    try:
        if not export_audio.exists():
            with audio.open('rb') as source, export_audio.open('xb') as new_audio:
                copied_audio = True
                shutil.copyfileobj(source, new_audio, length=1024 * 1024)
        with destination.open('x', encoding='utf-8-sig', newline='\n') as handle:
            created_map = True
            handle.write('\n'.join(lines))
    except BaseException:
        if created_map:
            destination.unlink(missing_ok=True)
        if copied_audio:
            export_audio.unlink(missing_ok=True)
        raise
    return ExportResult(str(destination), len(selected), len(values)-len(ms),
                        timing_mode, len(timing_lines), skipped, audio_name, copied_audio)
