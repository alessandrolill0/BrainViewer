"""Maps musical content and visitor pose to the activation of the seven lobes.

Each region is driven by the features that match its actual function: auditory
cortex (temporal) by energy, cerebellum by timing, parietal by groove, frontal
by valence, tonal ambiguity and observed movement, brainstem by arousal. The
occipital lobe is driven by the webcam, not by music, and the midline
structures represent co-activation of the others.

The backend computes the slow, semantic level; TouchDesigner adds the fast
modulation (the beat itself) from the real audio.
"""

import logging
import math

logger = logging.getLogger(__name__)

# Channel order of /lobes/activation. Must match regions.json.
REGION_ORDER = ['frontal', 'parietal', 'temporal', 'occipital',
                'cerebellum', 'brainstem', 'other_midline']

# Resting activation: no lobe ever goes fully dark, or the brain looks dead.
# The occipital lobe is the exception: with nothing to look at, visual cortex
# really is quiescent.
IDLE = {
    'frontal': 0.12, 'parietal': 0.10, 'temporal': 0.15, 'occipital': 0.03,
    'cerebellum': 0.10, 'brainstem': 0.18, 'other_midline': 0.08,
}

OCCIPITAL_PRESENT = 0.40   # someone is there, still and far
OCCIPITAL_MOVEMENT = 0.40  # added by movement
OCCIPITAL_PROXIMITY = 0.20  # added by closeness

# Base level of each region per structural section, before modulation.
SECTION_PROFILE = {
    'intro':  {'frontal': 0.35, 'parietal': 0.20, 'temporal': 0.55, 'cerebellum': 0.25, 'brainstem': 0.30},
    'verse':  {'frontal': 0.45, 'parietal': 0.45, 'temporal': 0.70, 'cerebellum': 0.45, 'brainstem': 0.45},
    'chorus': {'frontal': 0.60, 'parietal': 0.85, 'temporal': 0.90, 'cerebellum': 0.75, 'brainstem': 0.75},
    'bridge': {'frontal': 0.80, 'parietal': 0.40, 'temporal': 0.70, 'cerebellum': 0.45, 'brainstem': 0.50},
    'outro':  {'frontal': 0.30, 'parietal': 0.25, 'temporal': 0.55, 'cerebellum': 0.30, 'brainstem': 0.30},
}

# Frontal burst at a section boundary, where expectation is updated.
BOUNDARY_PULSE = 0.35
BOUNDARY_DECAY = 2.5

# How much visitor movement loads the frontal lobe. Added, not multiplied: it is
# an external event and must show even on a track that would not drive it.
FRONTAL_MOTION = 0.30

TEMPO_RANGE = (60.0, 170.0)
BEAT_CONFIDENCE_RANGE = (1.0, 3.5)   # RhythmExtractor2013 scale, not 0-1

# --- Divisive normalization ---------------------------------------------
#
# Summing many weighted features pulls every lobe toward the average, so all
# seven end up looking alike. Dividing each response by the overall activity of
# the group makes it legible *which* lobe dominates:
#
#     R_i = x_i^n / (sigma^n + mean(x^n))
#
# The absolute level is not lost: it travels separately as the `drive` gain.
# The occipital lobe stays out of the pool, since it is driven by the webcam.
MUSICAL_REGIONS = ['frontal', 'parietal', 'temporal', 'cerebellum', 'brainstem']
NORM_EXPONENT = 2.0
NORM_SIGMA = 0.70
DRIVE_FLOOR = 0.55        # how lit a quiet track stays
DRIVE_REFERENCE = 0.70    # raw mean that counts as full gain

# --- Synchrony (entrainment) --------------------------------------------
#
# How much the firing locks to the beat, 0-1. In TouchDesigner this decides
# between a unison flash on the beat and scattered, independent firing.
# Neural entrainment peaks near 120 BPM and weakens at the extremes; it is also
# always partial, hence the ceiling below 1.
SYNC_TEMPO_PEAK = 120.0
SYNC_TEMPO_WIDTH = 55.0
SYNC_CEILING = 0.85


def _clamp(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _norm(value: float, lo: float, hi: float) -> float:
    return _clamp((value - lo) / (hi - lo))


def section_at(sections: list[dict], position: float) -> dict | None:
    if not sections:
        return None
    # The first section starts at the first detected beat, not at 0: without
    # this the opening moments would fall through to the outro.
    if position < sections[0]['start']:
        return sections[0]
    for section in sections:
        if section['start'] <= position < section['end']:
            return section
    return sections[-1]


def normalize(values: dict) -> dict:
    """Divisive normalization over the music-driven lobes.

    Returns a new dict; non-musical regions pass through unchanged.
    """
    raw = [values[name] for name in MUSICAL_REGIONS]
    powered = [v ** NORM_EXPONENT for v in raw]
    pool = NORM_SIGMA ** NORM_EXPONENT + sum(powered) / len(powered)
    drive = DRIVE_FLOOR + (1.0 - DRIVE_FLOOR) * _norm(
        sum(raw) / len(raw), 0.0, DRIVE_REFERENCE)

    out = dict(values)
    for name, value in zip(MUSICAL_REGIONS, powered):
        out[name] = _clamp(value / pool * drive)
    return out


def synchrony(analysis: dict | None, position: float = 0.0) -> float:
    """How much the firing locks to the beat, 0-1.

    Not an activation level but a population statistic: at equal numbers of
    active neurons, it says how much they fire together. 0 with no music.
    """
    if not analysis:
        return 0.0

    beat_clarity = _norm(float(analysis.get('beat_confidence', 2.0)),
                         *BEAT_CONFIDENCE_RANGE)
    descriptors = analysis.get('descriptors') or {}
    danceability = _clamp(float(descriptors.get('danceability', 1.0)) / 2.5)

    bpm = float(analysis.get('bpm', 110.0))
    tempo_fit = math.exp(-((bpm - SYNC_TEMPO_PEAK) ** 2)
                         / (2 * SYNC_TEMPO_WIDTH ** 2))

    # A quiet section should not pull like a chorus, even within one track.
    section = section_at(analysis.get('sections') or [], position)
    energy = float(section.get('energy', 0.5)) if section else 0.5

    pulse = 0.6 * beat_clarity + 0.4 * danceability
    return round(_clamp(pulse * (0.45 + 0.55 * tempo_fit)
                        * (0.6 + 0.4 * energy)) * SYNC_CEILING, 3)


def frontal_motion(pose: dict | None) -> float:
    """How much visitor movement loads the frontal lobe, 0-1.

    Unlike the occipital lobe, the source does not matter here: webcam movement
    and mouse dragging both count, because both are actions.
    """
    if not pose:
        return 0.0
    return _clamp(float(pose.get('energy', 0.0)))


def occipital_from_pose(pose: dict | None) -> float:
    """Visual cortex: responds to who is in front, not to music.

    Webcam off and webcam on with an empty frame give the same minimum: it is
    presence that matters. Manual (mouse) pose does not count as presence — a
    cursor rotating a model is not someone the camera can see.
    """
    if not pose or not pose.get('present'):
        return IDLE['occipital']
    if pose.get('source') == 'manual':
        return IDLE['occipital']
    movement = float(pose.get('energy', 0.0))
    proximity = float(pose.get('scale', 0.0))
    return _clamp(OCCIPITAL_PRESENT
                  + OCCIPITAL_MOVEMENT * movement
                  + OCCIPITAL_PROXIMITY * proximity)


def idle_vector(pose: dict | None = None) -> list[float]:
    """The brain with no music: still alive, and already seeing.

    The two non-musical regions respond even in silence, so the installation is
    not inert until someone starts a track.
    """
    values = dict(IDLE)
    values['occipital'] = max(IDLE['occipital'], occipital_from_pose(pose))
    values['frontal'] = _clamp(values['frontal'] + FRONTAL_MOTION * frontal_motion(pose))
    return [round(values[name], 3) for name in REGION_ORDER]


def activation(analysis: dict, position: float, pose: dict | None = None) -> list[float]:
    """Activation of the 7 lobes for a track at a given position, in seconds."""
    if not analysis:
        return idle_vector(pose)

    sections = analysis.get('sections') or []
    section = section_at(sections, position)
    if section is None:
        return idle_vector(pose)

    profile = SECTION_PROFILE.get(section.get('label', 'verse'), SECTION_PROFILE['verse'])
    mood = analysis.get('mood') or {}
    valence = float(mood.get('valence', 0.5))
    arousal = float(mood.get('arousal', 0.5))
    descriptors = analysis.get('descriptors') or {}

    tempo = _norm(float(analysis.get('bpm', 110.0)), *TEMPO_RANGE)
    beat_clarity = _norm(float(analysis.get('beat_confidence', 2.0)), *BEAT_CONFIDENCE_RANGE)
    danceability = _clamp(float(descriptors.get('danceability', 1.0)) / 2.5)
    tonal_clarity = _clamp(float(analysis.get('key_strength', 0.5)))
    section_energy = float(section.get('energy', 0.5))

    out = {}

    # TEMPORAL — auditory cortex. Never drops to the minimum while music plays.
    out['temporal'] = max(0.35, profile['temporal'] * (0.75 + 0.25 * section_energy))

    # CEREBELLUM — timing and prediction. Tempo and beat regularity weigh
    # equally: a fast but irregular track is as demanding as a slow, precise one.
    out['cerebellum'] = profile['cerebellum'] * (0.5 + 0.5 * (0.5 * tempo + 0.5 * beat_clarity))

    # PARIETAL — sensorimotor integration, the urge to move.
    groove = 0.55 * danceability + 0.45 * beat_clarity
    out['parietal'] = profile['parietal'] * (0.45 + 0.55 * groove) * (0.7 + 0.3 * arousal)

    # FRONTAL — valence, tonal ambiguity, section boundary, visitor movement.
    ambiguity = 1.0 - tonal_clarity
    out['frontal'] = profile['frontal'] * (0.55 + 0.25 * valence + 0.20 * ambiguity)
    since_boundary = position - section['start']
    if since_boundary >= 0:
        out['frontal'] += BOUNDARY_PULSE * math.exp(-since_boundary / BOUNDARY_DECAY)
    out['frontal'] += FRONTAL_MOTION * frontal_motion(pose)

    # BRAINSTEM — fast subcortical route: arousal and energy.
    out['brainstem'] = profile['brainstem'] * (0.4 + 0.6 * (0.6 * arousal + 0.4 * section_energy))

    # Normalization runs on the five musical lobes only, before the regions
    # that depend on the others are computed.
    out['occipital'] = 0.0   # not part of the pool, see normalize()
    out = normalize(out)

    # OCCIPITAL — vision, not music. Assigned after normalization so it neither
    # suffers nor influences the contrast between the others.
    out['occipital'] = occipital_from_pose(pose)

    # MIDLINE — co-activation, read from the already normalized values.
    cortical = [out['frontal'], out['parietal'], out['temporal'], out['occipital']]
    out['other_midline'] = _clamp(0.5 * (sum(cortical) / len(cortical)))

    return [round(_clamp(max(out[name], IDLE[name])), 3) for name in REGION_ORDER]


def smooth(previous: list[float], target: list[float], factor: float) -> list[float]:
    """Move the current values toward the target.

    Section boundaries are sharp discontinuities; without this the whole brain
    would jump from one frame to the next.
    """
    if previous is None or len(previous) != len(target):
        return list(target)
    return [round(p + (t - p) * factor, 4) for p, t in zip(previous, target)]
