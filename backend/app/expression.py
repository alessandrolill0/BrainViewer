"""Facial expression: measured actions, plus an explicitly estimated reading.

Inferring emotion from a face is scientifically contested (Barrett et al., 2019),
so two things are kept separate and shown separately in the panel:

1. facial actions (smile, brow, jaw, eyes) — observable quantities taken from
   the MediaPipe Face Landmarker blendshapes;
2. valence and arousal — a combination of those, labelled as estimated.

This drives nothing in the render, so it never goes over OSC and the contract in
docs/protocol.md is unaffected.

Face Landmarker costs about 23 ms per frame against 5 ms for face_detection, so
it runs at 10 Hz on the frame the FaceTracker has already captured, while
face_detection remains the source of the pose.
"""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL = Path(__file__).resolve().parents[1] / 'models' / 'face_landmarker.task'

#: Frames analysed per second.
RATE_HZ = float(os.getenv('EXPRESSION_HZ', '10'))

#: Smoothing toward each new reading; at 10 Hz, 0.25 is about half a second.
SMOOTHING = 0.25

#: Below this a blendshape is model noise, not movement: a still face sits
#: around 0.02-0.10 (measured with bench_face).
NOISE_FLOOR = 0.12

NEUTRAL = {'smile': 0.0, 'brow': 0.0, 'jaw': 0.0, 'eyes': 0.0,
           'valence': 0.5, 'arousal': 0.0}

# --- Mood label ----------------------------------------------------------
#
# The two estimated quantities read as a point in Russell's (1980) affect
# circumplex: valence horizontal, arousal vertical, named quadrants. Since both
# axes are already estimates, the label is an estimate of an estimate and only
# appears once the face is far enough from neutral.
QUADRANTS = {
    (True, True): 'ELATED',      # high valence, high arousal
    (True, False): 'CALM',       # high valence, low arousal
    (False, True): 'TENSE',      # low valence, high arousal
    (False, False): 'SUBDUED',   # low valence, low arousal
}

#: Inside these distances from the centre nothing is claimed: NEUTRAL.
MOOD_VALENCE_BAND = 0.08   # |valence - 0.5|
MOOD_AROUSAL_BAND = 0.18


def mood_label(valence: float, arousal: float) -> tuple[str, float]:
    """Quadrant label and how pronounced it is, 0-1.

    The strength lets the panel flag a weak reading instead of presenting every
    word with the same confidence.
    """
    dv = valence - 0.5
    attivo = arousal >= MOOD_AROUSAL_BAND
    if abs(dv) < MOOD_VALENCE_BAND:
        # A mobilised face with no sign: something is happening, but not whether
        # it is good or bad.
        return ('AROUSED', _clamp(arousal)) if attivo else ('NEUTRAL', 0.0)
    label = QUADRANTS[(dv > 0, attivo)]
    strength = _clamp(max(abs(dv) * 2.0, arousal))
    return label, strength


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(min(hi, max(lo, value)))


def _clean(value: float) -> float:
    """Remove the noise floor and rescale, so 0 really means still."""
    return _clamp((value - NOISE_FLOOR) / (1.0 - NOISE_FLOOR))


class ExpressionReader:
    """Reads facial actions from an already captured and mirrored frame.

    It does not open the webcam and has no thread of its own: the FaceTracker
    loop calls it with the frame it already has.
    """

    def __init__(self):
        self.available = MODEL.exists()
        self._landmarker = None
        self._last_run = 0.0
        self._values = dict(NEUTRAL)
        self._present = False
        if not self.available:
            logger.info('face_landmarker.task assente: espressione disattivata '
                        '(si scarica con tools/download_models.py)')

    def _ensure_loaded(self) -> bool:
        """Load the model on first use, not at import: it costs a few hundred
        milliseconds and is only needed if the webcam is actually started."""
        if self._landmarker is not None:
            return True
        if not self.available:
            return False
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            self._mp = mp
            self._landmarker = vision.FaceLandmarker.create_from_options(
                vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=str(MODEL)),
                    # The blendshapes are the reason for this model; without the
                    # flag only the 478 points come back.
                    output_face_blendshapes=True,
                    num_faces=1,
                ))
            logger.info('Face Landmarker caricato: espressione attiva a %.0f Hz', RATE_HZ)
            return True
        except Exception as exc:
            self.available = False
            logger.warning('Face Landmarker non caricabile (%s): espressione disattivata', exc)
            return False

    def update(self, rgb_frame) -> None:
        """Call every frame: decides on its own whether it is time to run.

        The frame must already be RGB and already mirrored, the same one used
        for the pose — two readings on different images would be inconsistent.
        """
        now = time.monotonic()
        if now - self._last_run < 1.0 / RATE_HZ:
            return
        if not self._ensure_loaded():
            return
        self._last_run = now

        try:
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb_frame)
            result = self._landmarker.detect(image)
        except Exception:
            logger.exception('Lettura espressione fallita')
            return

        if not result.face_blendshapes:
            self._present = False
            self._decay()
            return

        self._present = True
        s = {c.category_name: c.score for c in result.face_blendshapes[0]}
        self._blend(self._from_blendshapes(s))

    @staticmethod
    def _from_blendshapes(s: dict) -> dict:
        """From the 52 blendshapes to four actions and the estimated pair."""
        smile = _clean((s.get('mouthSmileLeft', 0) + s.get('mouthSmileRight', 0)) / 2)
        frown = _clean((s.get('mouthFrownLeft', 0) + s.get('mouthFrownRight', 0)) / 2)
        brow_up = _clean(max(s.get('browInnerUp', 0),
                             (s.get('browOuterUpLeft', 0) + s.get('browOuterUpRight', 0)) / 2))
        brow_down = _clean((s.get('browDownLeft', 0) + s.get('browDownRight', 0)) / 2)
        jaw = _clean(s.get('jawOpen', 0))
        eyes = _clean((s.get('eyeWideLeft', 0) + s.get('eyeWideRight', 0)) / 2)

        # VALENCE — smile minus frown, with lowered brows as a third negative
        # term. Centred at 0.5: a still face is neither positive nor negative.
        valence = 0.5 + 0.5 * (smile - frown - 0.5 * brow_down)

        # AROUSAL — how mobilised the face is, regardless of sign. The gain is
        # for readability: the four terms never peak together, and without it
        # arousal would stay in the lower third of the bar.
        arousal = 1.8 * (0.45 * brow_up + 0.30 * jaw + 0.15 * eyes + 0.10 * smile)

        # `brow` is brow movement in either direction: the sign is carried by
        # valence, so two separate bars would say less.
        return {
            'smile': smile,
            'brow': max(brow_up, brow_down),
            'jaw': jaw,
            'eyes': eyes,
            'valence': _clamp(valence),
            'arousal': _clamp(arousal),
        }

    def _blend(self, target: dict) -> None:
        for key, value in target.items():
            self._values[key] += (value - self._values[key]) * SMOOTHING

    def _decay(self) -> None:
        """No face: drift back to neutral instead of freezing the last one."""
        self._blend(NEUTRAL)

    def reset(self) -> None:
        self._values = dict(NEUTRAL)
        self._present = False

    def state(self) -> dict:
        label, strength = mood_label(self._values['valence'], self._values['arousal'])
        return {
            'available': self.available,
            'present': self._present,
            'rate_hz': RATE_HZ,
            # Computed here, not in the panel: interpretations belong where they
            # are documented.
            'mood': label,
            'mood_strength': round(strength, 3),
            **{k: round(v, 3) for k, v in self._values.items()},
        }
