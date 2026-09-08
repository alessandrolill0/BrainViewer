"""Maps musical content and visitor pose to the activation of the seven lobes.

Each region is driven by the features that match its actual function: auditory
cortex (temporal) by energy, cerebellum by timing, parietal by groove, frontal
by valence, tonal ambiguity and observed movement, brainstem by arousal — both
the track's and, when the webcam sees a face, the visitor's own. The
occipital lobe is driven by the webcam, not by music, and the midline
structures represent co-activation of the others.

What decides the moment is the local feature curves of the track (see
analysis._timeline): the activation follows how the track is going right now,
not a structural label. Earlier versions worked from named sections
(intro/verse/chorus), which on instrumentals, loops and ambient material meant
running the mapping on a song form that was not in the signal.

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

# Frontal burst on timbral change, where the prediction about what is coming
# has to be updated. This used to fire at structural section boundaries; it now
# follows the `novelty` curve, which is measured continuously instead of being
# derived from a song form that most of this material does not have.
NOVELTY_PULSE = 0.30

# What each moment of the track is worth to each lobe, before normalization.
# Read as: base level, then how much of the range the local curves control.
# The five entries are the five music-driven lobes; the curve names are the
# ones produced by analysis._timeline().
LOCAL_WEIGHTS = {
    # Auditory cortex: it hears whatever is playing, loud or quiet. Absolute
    # energy first, so a subdued track reads as subdued.
    'temporal': {'base': 0.35, 'energy': 0.40, 'dynamics': 0.25},
    # Timing and prediction: driven by how much there is to lock onto right now.
    'cerebellum': {'base': 0.20, 'percussive': 0.55, 'dynamics': 0.25},
    # The urge to move: percussive drive, with loudness behind it.
    'parietal': {'base': 0.15, 'percussive': 0.60, 'energy': 0.25},
    # Interpretation: works hardest where the music changes.
    'frontal': {'base': 0.25, 'novelty': 0.45, 'dynamics': 0.30},
    # Fast subcortical route: gross energy and brightness, no subtlety. It
    # needs `dynamics` alongside `energy` to move at all: absolute loudness
    # barely varies within a mastered track, so on that alone this lobe sat
    # frozen at its resting value for entire pieces.
    'brainstem': {'base': 0.20, 'energy': 0.35, 'dynamics': 0.30, 'brightness': 0.15},
}

# How much visitor movement loads the frontal lobe. Added, not multiplied: it is
# an external event and must show even on a track that would not drive it.
FRONTAL_MOTION = 0.30

# How much the visitor's own arousal, read from the face, adds to the arousal
# the track carries. Both reach the brainstem because they are the same
# quantity — the organism's level of activation — and the brainstem is where it
# is regulated: the reticular activating system, locus coeruleus, raphe nuclei.
# The two are added rather than blended: the music's arousal is a property of
# the track, the face's is a measurement of the person, and one does not replace
# the other. A still face contributes exactly zero (expression.NEUTRAL), so this
# only ever pushes upward.
#
# What the face gives is an *action*, not an emotion: brow, jaw and eye opening
# are measured, and calling their combination "arousal" is the one estimate
# accepted here — it stays on the axis that has a real brainstem substrate,
# unlike valence, whose substrate is ventral striatum and cingulate and does not
# exist as a separate region in this model.
FACE_AROUSAL = 0.35

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


def _above(value: float, middle: float = 0.5) -> float:
    """Only what exceeds the middle, rescaled to 0-1.

    For curves that are normalized within the track: their mean is fixed by
    construction, so it carries no information and only the excess does.
    """
    return _clamp((value - middle) / (1.0 - middle))


NEUTRAL_LOCAL = {'energy': 0.5, 'dynamics': 0.5, 'percussive': 0.5,
                 'brightness': 0.5, 'novelty': 0.3}


def local_at(analysis: dict | None, position: float) -> dict | None:
    """The five local curves sampled at `position`, in seconds.

    Linear interpolation between the two nearest samples: the curves are at
    2 Hz while this is called at 30 Hz, and stepping between samples would show
    as a stutter across the whole brain.

    Returns None if the track has no timeline — analyses produced before this
    existed. The caller decides what to do about it.
    """
    timeline = (analysis or {}).get('timeline')
    if not timeline or not timeline.get('samples'):
        return None

    hz = float(timeline.get('hz') or 2.0)
    n = int(timeline['samples'])
    x = min(max(position * hz, 0.0), n - 1.0)
    i = int(x)
    j = min(i + 1, n - 1)
    frac = x - i

    out = {}
    for name, default in NEUTRAL_LOCAL.items():
        curve = timeline.get(name)
        if not curve:
            out[name] = default
            continue
        a, b = float(curve[i]), float(curve[j])
        out[name] = a + (b - a) * frac
    return out


def _from_curves(weights: dict, local: dict) -> float:
    """Base level plus each curve times its weight."""
    value = weights['base']
    for name, weight in weights.items():
        if name != 'base':
            value += weight * local[name]
    return value


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

    # A quiet moment should not lock the brain to the beat the way a full one
    # does — and a passage with no percussion has no beat to lock to, however
    # danceable the track is on average.
    local = local_at(analysis, position) or dict(NEUTRAL_LOCAL)
    here = 0.6 * local['percussive'] + 0.4 * local['dynamics']

    pulse = 0.6 * beat_clarity + 0.4 * danceability
    return round(_clamp(pulse * (0.45 + 0.55 * tempo_fit)
                        * (0.4 + 0.6 * here)) * SYNC_CEILING, 3)


def frontal_motion(pose: dict | None) -> float:
    """How much visitor movement loads the frontal lobe, 0-1.

    Unlike the occipital lobe, the source does not matter here: webcam movement
    and mouse dragging both count, because both are actions.
    """
    if not pose:
        return 0.0
    return _clamp(float(pose.get('energy', 0.0)))


def floors(expression: dict | None = None) -> dict:
    """Resting level of each region, given who is in front of the screen.

    Only the brainstem moves, and it has to: with a fixed floor its share of
    the visitor's arousal was swallowed by the clamp for 11-73% of a track,
    depending on how loud the track is — the face would do something sometimes
    and nothing the rest of the time, with nothing to explain the difference.
    A raised resting level is also the honest reading: baseline activation of
    the organism is what these nuclei regulate, music or no music.
    """
    out = dict(IDLE)
    out['brainstem'] = _clamp(out['brainstem'] + FACE_AROUSAL * face_arousal(expression))
    return out


def face_arousal(expression: dict | None) -> float:
    """The visitor's arousal read from the face, 0-1, or 0 if there is no face.

    Absent webcam, absent face and neutral face all give zero, and they should:
    the term is additive, so zero means the track alone decides.
    """
    if not expression or not expression.get('present'):
        return 0.0
    return _clamp(float(expression.get('arousal', 0.0)))


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


def idle_vector(pose: dict | None = None, expression: dict | None = None) -> list[float]:
    """The brain with no music: still alive, and already seeing.

    The non-musical regions respond even in silence, so the installation is not
    inert until someone starts a track: the occipital lobe to presence, the
    frontal to movement, the brainstem to the visitor's own arousal.
    """
    values = floors(expression)
    values['occipital'] = max(IDLE['occipital'], occipital_from_pose(pose))
    values['frontal'] = _clamp(values['frontal'] + FRONTAL_MOTION * frontal_motion(pose))
    return [round(values[name], 3) for name in REGION_ORDER]


def raw_activation(analysis: dict, position: float, pose: dict | None = None,
                   expression: dict | None = None) -> dict:
    """The five music-driven lobes before normalization.

    Split out from activation() because it is the only place where the balance
    between lobes can actually be read: after divisive normalization a lobe
    that is merely lower than the others looks extinguished, which makes it
    impossible to tell an unbalanced weight from a working one.
    """
    # Tracks analysed before the timeline existed fall back to a neutral
    # moment: the mapping still runs, it just cannot follow the track. They
    # need re-analysing; `local_at` returning None is how you find out.
    local = local_at(analysis, position) or dict(NEUTRAL_LOCAL)

    mood = analysis.get('mood') or {}
    valence = float(mood.get('valence', 0.5))
    arousal = float(mood.get('arousal', 0.5))
    descriptors = analysis.get('descriptors') or {}

    tempo = _norm(float(analysis.get('bpm', 110.0)), *TEMPO_RANGE)
    beat_clarity = _norm(float(analysis.get('beat_confidence', 2.0)), *BEAT_CONFIDENCE_RANGE)
    danceability = _clamp(float(descriptors.get('danceability', 1.0)) / 2.5)
    tonal_clarity = _clamp(float(analysis.get('key_strength', 0.5)))

    # The local curves set what the track is doing now; the global descriptors
    # (tempo, mood, tonality) modulate it. What changed with the timeline is
    # which of the two leads: it used to be the section profile, i.e. a label.
    out = {}

    # TEMPORAL — auditory cortex. Never drops to the minimum while music plays,
    # whatever the moment: there is always something to hear.
    out['temporal'] = max(0.35, _from_curves(LOCAL_WEIGHTS['temporal'], local))

    # CEREBELLUM — timing and prediction. Tempo and beat regularity weigh
    # equally: a fast but irregular track is as demanding as a slow, precise one.
    out['cerebellum'] = (_from_curves(LOCAL_WEIGHTS['cerebellum'], local)
                         * (0.5 + 0.5 * (0.5 * tempo + 0.5 * beat_clarity)))

    # PARIETAL — sensorimotor integration, the urge to move. The percussive
    # curve carries the moment; danceability and beat clarity say how much of a
    # groove the track has at all. One modulation, not two: with groove and
    # arousal in cascade this lobe was the only one multiplied twice, and it
    # sat near zero on every track in the library.
    groove = 0.55 * danceability + 0.45 * beat_clarity
    out['parietal'] = (_from_curves(LOCAL_WEIGHTS['parietal'], local)
                       * (0.65 + 0.35 * groove))

    # FRONTAL — valence, tonal ambiguity, musical change, visitor movement.
    ambiguity = 1.0 - tonal_clarity
    out['frontal'] = (_from_curves(LOCAL_WEIGHTS['frontal'], local)
                      * (0.55 + 0.25 * valence + 0.20 * ambiguity))
    # Only the part above average counts as change: `novelty` is scaled within
    # the track, so its mean is 0.5 by construction and a plain multiple of it
    # would be a constant bonus rather than a response to anything.
    out['frontal'] += NOVELTY_PULSE * _above(local['novelty'])
    out['frontal'] += FRONTAL_MOTION * frontal_motion(pose)

    # BRAINSTEM — fast subcortical route: arousal and energy. The visitor's own
    # arousal, measured on the face, adds to the track's: same quantity, two
    # sources, and this is the structure that regulates it.
    aroused = _clamp(arousal + FACE_AROUSAL * face_arousal(expression))
    out['brainstem'] = (_from_curves(LOCAL_WEIGHTS['brainstem'], local)
                        * (0.55 + 0.45 * aroused))

    return out


def activation(analysis: dict, position: float, pose: dict | None = None,
               expression: dict | None = None) -> list[float]:
    """Activation of the 7 lobes for a track at a given position, in seconds."""
    if not analysis:
        return idle_vector(pose, expression)

    out = raw_activation(analysis, position, pose, expression)

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

    floor = floors(expression)
    return [round(_clamp(max(out[name], floor[name])), 3) for name in REGION_ORDER]


def smooth(previous: list[float], target: list[float], factor: float) -> list[float]:
    """Move the current values toward the target.

    The local curves are already smoothed, but pose and the normalization pool
    are not: without this the whole brain would jump from one frame to the next.
    """
    if previous is None or len(previous) != len(target):
        return list(target)
    return [round(p + (t - p) * factor, 4) for p, t in zip(previous, target)]
