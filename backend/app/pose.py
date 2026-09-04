"""Head tracking from the webcam with MediaPipe, sent to TouchDesigner.

The interaction is a mirror: the visitor stands in front of the screen and the
brain moves with them. The frame is flipped horizontally as soon as it is read,
before MediaPipe sees it, so everything downstream is already in screen
coordinates and translations need no further inversion in TouchDesigner. Note
that MediaPipe's left/right labels then refer to the sides of the screen.

Rotations about the vertical axis are the exception: a mirror reverses the sense
of rotation about axes lying in its plane, so `turn` is multiplied by a negative
value in the Rotate Y expression in TouchDesigner. The sign lives there because
the manual pose reaches TD by a different path and the two only meet at that
point.

face_detection is used rather than full body landmarks: in front of a screen the
head is the only reliably framed part, and six points give position, distance,
tilt and rotation.

Output conventions:
    x        0 = left edge of the screen, 1 = right
    y        0 = top, 1 = bottom
    scale    0-1, closeness, from the apparent size of the face
    energy   0-1, amount of movement
    lean     -1..+1, head roll, 0 = upright
    turn     -1..+1, head yaw, 0 = facing forward
    pitch    -1..+1, head tilt, 0 = looking at the horizon

Capture runs in its own thread: cv2.VideoCapture.read() is blocking and would
stall the whole backend.
"""

import logging
import math
import os
import threading
import time

import cv2
import mediapipe as mp
import numpy as np

from .expression import ExpressionReader

logger = logging.getLogger(__name__)

# The six points MediaPipe returns for each detected face.
RIGHT_EYE, LEFT_EYE, NOSE_TIP, MOUTH, RIGHT_EAR, LEFT_EAR = range(6)

# Apparent face size (fraction of frame width) at "far" and "near" distance. If
# `scale` in /api/health sticks at 0 or 1, these do not match your setup.
FACE_SPAN = (float(os.getenv('POSE_SPAN_FAR', '0.08')),
             float(os.getenv('POSE_SPAN_NEAR', '0.45')))

# Head tilt corresponding to lean = +-1, in degrees.
LEAN_FULL_SCALE = float(os.getenv('POSE_LEAN_DEGREES', '25'))

# Nose offset from the eye centre, in units of interocular distance, giving
# turn = +-1. Only applies to the webcam: the manual pose reaches +-1 by itself,
# and its knob is DRAG_GAIN in frontend/src/components/Stage.jsx.
TURN_FULL_SCALE = float(os.getenv('POSE_TURN_RATIO', '0.42'))

# Same measure on the vertical, for forward/backward tilt. With the head upright
# the nose already sits below the eye line, and how far depends on the camera
# angle: the real neutral is set by /api/pose/calibrate.
PITCH_BASELINE = float(os.getenv('POSE_PITCH_BASELINE', '0.18'))
PITCH_FULL_SCALE = float(os.getenv('POSE_PITCH_RATIO', '0.12'))
TURN_BASELINE = float(os.getenv('POSE_TURN_BASELINE', '0.0'))

# --- Movement energy ----------------------------------------------------
#
# A speed, not a per-frame displacement: measuring per frame would make it
# depend on the frame rate. In normalized units per second, so 0.55 means that
# crossing half the frame in one second counts as full energy.
ENERGY_FULL_SCALE = float(os.getenv('POSE_ENERGY_RATIO', '0.55'))

# Landmarks jitter by a few thousandths even on a still face; without removing
# that, energy would never return to zero.
ENERGY_NOISE = 0.06

# Immediate attack, half-life release: movement must show as it happens and then
# fade. Same half-life as ManualPose, so mouse and webcam behave alike.
ENERGY_HALF_LIFE = 0.35

# A face lost for less than this does not switch everything off: turning your
# head should not collapse the installation.
PRESENCE_GRACE = 0.8


def _clamp(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _clamp_signed(value: float) -> float:
    return float(min(1.0, max(-1.0, value)))


class FaceTracker:
    """Reads the webcam, locates the head and sends it to TouchDesigner."""

    def __init__(self, osc, camera_index: int | None = None,
                 width: int | None = None, height: int | None = None,
                 model_selection: int | None = None):
        # Configurable without touching the code: POSE_CAMERA, POSE_WIDTH,
        # POSE_HEIGHT, POSE_MODEL (0 = faces within ~2 m, 1 = up to ~5 m).
        self.osc = osc
        self.camera_index = int(os.getenv('POSE_CAMERA', camera_index or 0))
        self.width = int(os.getenv('POSE_WIDTH', width or 640))
        self.height = int(os.getenv('POSE_HEIGHT', height or 480))
        self.model_selection = int(os.getenv('POSE_MODEL', model_selection or 0))

        # Neutral pose, updatable at runtime with calibrate(): it depends on the
        # camera angle and changes from one setup to the next.
        self.pitch_baseline = PITCH_BASELINE
        self.turn_baseline = TURN_BASELINE
        self._raw = {'drop': None, 'offset': None}

        # Expression rides on the same frame as the pose, at a lower rate.
        self.expression = ExpressionReader()

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = {
            'present': False, 'x': 0.5, 'y': 0.5, 'scale': 0.0,
            'energy': 0.0, 'lean': 0.0, 'turn': 0.0, 'pitch': 0.0, 'fps': 0.0,
        }
        self.error: str | None = None

    # --- lifecycle ------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self.error = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='face', daemon=True)
        self._thread.start()
        logger.info('Tracciamento del volto avviato (camera %d)', self.camera_index)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        with self._lock:
            self._state.update({'present': False, 'energy': 0.0, 'scale': 0.0,
                                'lean': 0.0, 'turn': 0.0, 'pitch': 0.0})
        # Otherwise the panel would keep showing the expression of the last
        # person seen, with the webcam off.
        self.expression.reset()
        self.osc.send('/pose/present', 0)
        logger.info('Tracciamento del volto fermato')

    def state(self) -> dict:
        with self._lock:
            return {**self._state, 'running': self.running, 'error': self.error,
                    'pitch_baseline': round(self.pitch_baseline, 4),
                    'turn_baseline': round(self.turn_baseline, 4)}

    def calibrate(self) -> dict:
        """Adopt the current pose as neutral.

        How far the nose sits below the eye line depends on the camera angle and
        changes with every setup, so it is measured rather than guessed.
        """
        with self._lock:
            present = self._state['present']
        if not present or self._raw['drop'] is None:
            raise RuntimeError('nessun volto inquadrato: mettiti davanti alla webcam, '
                               'dritto e fermo, e riprova')
        self.pitch_baseline = self._raw['drop']
        self.turn_baseline = self._raw['offset']
        logger.info('Posa neutra calibrata: pitch_baseline=%.3f turn_baseline=%.3f',
                    self.pitch_baseline, self.turn_baseline)
        return self.state()

    # --- processing -----------------------------------------------------

    def _run(self) -> None:
        capture = cv2.VideoCapture(self.camera_index)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if not capture.isOpened():
            # On macOS the camera permission prompt must come from the main
            # thread, and this is a worker thread.
            self.error = (f'webcam {self.camera_index} non apribile. Concedi il permesso '
                          'una volta sola lanciando:  python tools/check_camera.py  '
                          '(su macOS la richiesta non puo partire da un thread secondario). '
                          'Se il permesso c e gia, la camera potrebbe essere occupata da '
                          'un altro programma.')
            logger.error(self.error)
            return

        detector = mp.solutions.face_detection.FaceDetection(
            model_selection=self.model_selection, min_detection_confidence=0.5)

        previous: np.ndarray | None = None
        previous_t = 0.0
        energy = 0.0
        last_seen = 0.0
        frame_times: list[float] = []

        try:
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                now = time.monotonic()
                frame_times = [t for t in frame_times if now - t < 1.0] + [now]

                # The mirror: flipped here, once.
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = detector.process(rgb)

                # Same frame, already converted and mirrored: two readings on
                # different images would be inconsistent. Decides on its own
                # whether it is time (10 Hz against 30 for the pose).
                self.expression.update(rgb)

                if not result.detections:
                    if now - last_seen > PRESENCE_GRACE:
                        self._publish_absent(len(frame_times))
                        previous = None
                        energy = 0.0
                    continue

                last_seen = now
                # With several people in frame, follow the closest one, i.e. the
                # largest face: picking at random would make the brain jump.
                detection = max(result.detections,
                                key=lambda d: d.location_data.relative_bounding_box.width)
                box = detection.location_data.relative_bounding_box
                points = np.array([[k.x, k.y] for k in
                                   detection.location_data.relative_keypoints],
                                  dtype=np.float32)

                dt = now - previous_t if previous_t else 0.0
                if previous is not None and len(previous) == len(points) and dt > 0:
                    moved = float(np.linalg.norm(points - previous, axis=1).mean())
                    speed = max(0.0, moved / dt - ENERGY_NOISE)
                    target = _clamp(speed / ENERGY_FULL_SCALE)
                    energy = (target if target > energy
                              else energy * 0.5 ** (dt / ENERGY_HALF_LIFE))
                previous = points
                previous_t = now

                self._publish(box, points, energy, len(frame_times))
        finally:
            capture.release()
            detector.close()

    def _publish_absent(self, fps: int) -> None:
        with self._lock:
            self._state.update({'present': False, 'energy': 0.0, 'fps': float(fps)})
        self.osc.send('/pose/present', 0)
        self.osc.send('/pose/energy', 0.0)

    def _publish(self, box, points: np.ndarray, energy: float, fps: int) -> None:
        x = _clamp(box.xmin + box.width / 2)
        y = _clamp(box.ymin + box.height / 2)
        scale = _clamp((box.width - FACE_SPAN[0]) / (FACE_SPAN[1] - FACE_SPAN[0]))

        lean = turn = pitch = 0.0
        eye_left, eye_right = points[LEFT_EYE], points[RIGHT_EYE]
        eye_distance = float(np.linalg.norm(eye_left - eye_right))
        if eye_distance > 1e-3:
            dx = float(eye_right[0] - eye_left[0])
            dy = float(eye_right[1] - eye_left[1])
            angle = math.degrees(math.atan2(dy, dx))
            # After the flip dx can be negative and the angle comes out near
            # +-180 instead of near 0, pinning the value at +-1.
            if angle > 90:
                angle -= 180
            elif angle < -90:
                angle += 180
            lean = _clamp_signed(angle / LEAN_FULL_SCALE)

            # Turning the head moves the nose toward one eye. Measured in units
            # of interocular distance, so it does not depend on how close you are.
            eye_center_x = (eye_left[0] + eye_right[0]) / 2
            offset = (float(points[NOSE_TIP][0]) - float(eye_center_x)) / eye_distance
            turn = _clamp_signed((offset - self.turn_baseline) / TURN_FULL_SCALE)

            # Pitch: same idea on the vertical, but relative to the height of the
            # face box, not the eye distance. Turning the head foreshortens the
            # eye distance, which would make the ratio grow on its own.
            eye_center_y = (eye_left[1] + eye_right[1]) / 2
            reference = max(float(box.height), 1e-3)
            drop = (float(points[NOSE_TIP][1]) - float(eye_center_y)) / reference
            pitch = _clamp_signed((drop - self.pitch_baseline) / PITCH_FULL_SCALE)
            # Raw measures, adopted as zero by calibrate().
            self._raw = {'drop': drop, 'offset': offset}

        with self._lock:
            self._state.update({
                'present': True, 'x': round(x, 4), 'y': round(y, 4),
                'scale': round(scale, 4), 'energy': round(float(energy), 4),
                'lean': round(lean, 4), 'turn': round(turn, 4),
                'pitch': round(pitch, 4), 'fps': float(fps),
            })

        # One argument per address: TouchDesigner names channels from
        # multi-argument messages ambiguously, while this way the channel name
        # is exactly the address.
        self.osc.send('/pose/present', 1)
        self.osc.send('/pose/x', x)
        self.osc.send('/pose/y', y)
        self.osc.send('/pose/scale', scale)
        self.osc.send('/pose/energy', float(energy))
        self.osc.send('/pose/lean', lean)
        self.osc.send('/pose/turn', turn)
        self.osc.send('/pose/pitch', pitch)


class ManualPose:
    """Pose driven from the panel, for when there is no webcam.

    It sends the same OSC addresses as face tracking, so TouchDesigner cannot
    tell the two apart — which is also why they cannot run at the same time.
    """

    #: Seconds of silence after which the manual pose releases itself. A safety
    #: net, not the normal mechanism: the panel sends pose.manual.release at the
    #: end of a gesture. Without it the backend would keep believing someone is
    #: in front of the webcam and the occipital lobe would never go dark again.
    STALE_AFTER = 2.0

    #: How much mouse movement counts as energy = 1, in pose units per call.
    #: Energy accumulates between messages, so a decisive gesture reaches full
    #: scale in a couple of tenths while a slow one does not.
    ENERGY_GAIN = 8.0

    #: Half-life of the energy once the mouse stops.
    ENERGY_HALF_LIFE = 0.25

    def __init__(self, osc):
        self.osc = osc
        self.active = False
        self._last_command = 0.0
        self._energy = 0.0
        self._values = {'x': 0.5, 'y': 0.5, 'scale': 0.5,
                        'turn': 0.0, 'pitch': 0.0, 'lean': 0.0}

    def apply(self, **changes) -> dict:
        # How much the pose changed since the last message: this is the
        # visitor's "movement" when driving with the mouse.
        movimento = 0.0
        for key, value in changes.items():
            if key not in self._values or value is None:
                continue
            value = float(value)
            value = (_clamp(value) if key in ('x', 'y', 'scale')
                     else _clamp_signed(value))
            movimento += abs(value - self._values[key])
            self._values[key] = value

        self._energy = _clamp(self.energy() + movimento * self.ENERGY_GAIN)

        first = not self.active
        self.active = True
        self._last_command = time.monotonic()
        if first:
            logger.info('Posa manuale attivata dal pannello')

        self.osc.send('/pose/present', 1)
        for key, value in self._values.items():
            self.osc.send(f'/pose/{key}', value)
        # Energy is not zero here: dragging the model is an action by the
        # visitor and feeds the frontal lobe. It stays excluded from *presence*,
        # which is what the occipital lobe looks at.
        self.osc.send('/pose/energy', self.energy())
        return self.state()

    def energy(self) -> float:
        """Ongoing movement, fading on its own when the mouse stops.

        Decay is computed on read rather than in a loop: nothing polls the
        manual pose, so otherwise the energy would stay at its last value.
        """
        if self._energy <= 0.0:
            return 0.0
        trascorso = time.monotonic() - self._last_command
        return _clamp(self._energy * 0.5 ** (trascorso / self.ENERGY_HALF_LIFE))

    def release(self) -> dict:
        """Stop driving. Orientation stays where it was, only presence is
        cleared, so the model does not jump away when the button is released."""
        if self.active:
            self.active = False
            self.osc.send('/pose/present', 0)
            logger.info('Posa manuale disattivata')
        return self.state()

    def release_if_stale(self) -> bool:
        """Release if no commands have arrived for a while. See STALE_AFTER."""
        if not self.active:
            return False
        if time.monotonic() - self._last_command < self.STALE_AFTER:
            return False
        logger.info('Posa manuale scaduta dopo %.0f s di silenzio', self.STALE_AFTER)
        self.release()
        return True

    def state(self) -> dict:
        return {**self._values, 'present': self.active, 'energy': round(self.energy(), 3),
                'fps': 0.0, 'running': False, 'error': None, 'source': 'manual'}
