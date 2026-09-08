"""Offline track analysis: rhythm, key, mood, and the local feature curves.

Runs once per track after upload; the result is stored in the library. Real-time
analysis is a separate concern and lives in TouchDesigner.

Essentia does the signal work, librosa the curves that say how the track is
going moment by moment (see _timeline). The audio is decoded once and passed to
both.

Mood comes either from pre-trained models (essentia-tensorflow plus the .pb
files in backend/models/, fetched with tools/download_models.py) or, if they are
missing, from a weighted heuristic. `mood.source` always says which was used.
"""

import logging
from pathlib import Path

import essentia.standard as es
import librosa
import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100
SEGMENT_SR = 22050          # half the bandwidth is enough for segmentation
HOP = 512

# Bars of the waveform sent to the panel; matches WAVE_BARS in Player.jsx.
WAVEFORM_BARS = 104

# Local feature curves: how the track is going at a given moment. 2 Hz is
# plenty — these drive slow, semantic levels, and TouchDesigner adds the fast
# modulation from the real audio.
TIMELINE_HZ = 2.0
TIMELINE_SMOOTH = 1.5       # seconds of smoothing over the curves

# Absolute full scale of the local RMS: it is what lets a quiet track stay
# quiet next to a loud one. Measured over the library — median 0.205, 99th
# percentile 0.470 — so 0.45 keeps some headroom instead of clipping the loud
# tracks to a flat 1.0, which is exactly how a channel dies.
TIMELINE_ENERGY_RANGE = (0.02, 0.45)

# --- TensorFlow mood models ---------------------------------------------
MODELS_DIR = Path(__file__).resolve().parents[1] / 'models'
EMBEDDINGS_MODEL = MODELS_DIR / 'msd-musicnn-1.pb'
EMOMUSIC_MODEL = MODELS_DIR / 'emomusic-msd-musicnn-1.pb'

MUSICNN_SR = 16000          # musicnn models are trained at 16 kHz
EMOMUSIC_SCALE = (1.0, 9.0)  # emomusic predicts on a 1-9 scale, we want 0-1

# Building a TensorFlow graph is expensive: instantiate once and reuse.
_tf_models: tuple | None = None

# --- heuristic mood weights, each group sums to 1 ------------------------
AROUSAL_WEIGHTS = {'tempo': 0.35, 'energy': 0.30, 'danceability': 0.20, 'intensity': 0.15}
VALENCE_WEIGHTS = {'mode': 0.45, 'tempo': 0.25, 'brightness': 0.20, 'danceability': 0.10}

# Ranges used to normalize to 0-1; values outside saturate.
TEMPO_RANGE = (60.0, 170.0)
ENERGY_RANGE = (0.01, 0.25)
BRIGHTNESS_RANGE = (800.0, 4000.0)
DANCEABILITY_RANGE = (0.0, 2.5)


def _norm(value: float, lo: float, hi: float) -> float:
    return float(min(1.0, max(0.0, (value - lo) / (hi - lo))))


def models_available() -> bool:
    return EMBEDDINGS_MODEL.is_file() and EMOMUSIC_MODEL.is_file()


def _load_tf_models():
    """Instantiate the two cascaded mood models, once."""
    global _tf_models
    if _tf_models is None:
        # Node names are not the algorithm defaults: they come from the schema
        # declared in emomusic-msd-musicnn-1.json.
        _tf_models = (
            es.TensorflowPredictMusiCNN(graphFilename=str(EMBEDDINGS_MODEL),
                                        output='model/dense/BiasAdd'),
            es.TensorflowPredict2D(graphFilename=str(EMOMUSIC_MODEL),
                                   input='flatten_in_input', output='dense_out'),
        )
    return _tf_models


def _mood_from_model(audio: np.ndarray) -> dict | None:
    """Valence and arousal from the pre-trained models, or None if unusable."""
    if not models_available():
        return None
    try:
        embedder, regressor = _load_tf_models()
        audio16 = librosa.resample(audio, orig_sr=SAMPLE_RATE, target_sr=MUSICNN_SR)
        predictions = np.asarray(regressor(embedder(audio16)))
        if predictions.ndim == 1:
            predictions = predictions[np.newaxis, :]
        # One prediction per window: the mean describes the whole track.
        valence, arousal = predictions.mean(axis=0)[:2]
        lo, hi = EMOMUSIC_SCALE
        return {
            'valence': round(_norm(float(valence), lo, hi), 3),
            'arousal': round(_norm(float(arousal), lo, hi), 3),
            'source': 'emomusic-msd-musicnn',
        }
    except Exception as exc:
        # A model that fails to load must not sink the whole analysis: bpm, key
        # and structure are still valid.
        logger.warning('mood da modello non riuscito (%s), uso l\'euristica', exc)
        return None


def _resample_curve(values: np.ndarray, n: int) -> np.ndarray:
    """Average `values` down to `n` samples, evenly spaced over its length."""
    if n <= 0 or values.size == 0:
        return np.zeros(max(n, 0))
    edges = np.linspace(0, values.size, n + 1).astype(int)
    return np.array([values[a:b].mean() if b > a else values[min(a, values.size - 1)]
                     for a, b in zip(edges[:-1], edges[1:])])


def _relative(values: np.ndarray) -> np.ndarray:
    """Scale to 0-1 on the track's own 5th-95th percentile range.

    Percentiles rather than min/max: a single silent frame or one clipped peak
    would otherwise set the whole scale.
    """
    lo, hi = np.percentile(values, 5), np.percentile(values, 95)
    if hi - lo < 1e-9:
        return np.full_like(values, 0.5)
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)


def _timeline(audio: np.ndarray, duration: float) -> dict:
    """Local feature curves along the track, sampled at TIMELINE_HZ.

    This is what tells the mapping how the track is going *right now*. It
    replaces the earlier structural sections (intro/verse/chorus): on this kind
    of material — instrumentals, loops, ambient — song form is often simply not
    present in the signal, and labelling it anyway meant the mapping ran on an
    invented structure. A continuous envelope makes no claim it cannot keep.

    Two of the curves are on an absolute scale and two are relative to the
    track, on purpose: `energy` and `brightness` let a quiet track stay quiet
    compared to a loud one, while `dynamics`, `percussive` and `novelty` are the
    internal contrast, which only means anything within one piece.
    """
    n = int(max(2, round(duration * TIMELINE_HZ)))
    y = librosa.resample(audio, orig_sr=SAMPLE_RATE, target_sr=SEGMENT_SR)

    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=SEGMENT_SR, hop_length=HOP)[0]
    onset = librosa.onset.onset_strength(y=y, sr=SEGMENT_SR, hop_length=HOP)

    # Timbral change: distance between consecutive MFCC frames, without the
    # first coefficient, which is essentially log-energy and would make this
    # curve a duplicate of `dynamics`.
    mfcc = librosa.feature.mfcc(y=y, sr=SEGMENT_SR, n_mfcc=13, hop_length=HOP)[1:]
    scale = mfcc.std(axis=1, keepdims=True)
    scale[scale < 1e-9] = 1.0
    mfcc = mfcc / scale
    change = np.concatenate([[0.0], np.linalg.norm(np.diff(mfcc, axis=1), axis=0)])

    curves = {
        'energy': _norm_array(_resample_curve(rms, n), *TIMELINE_ENERGY_RANGE),
        'dynamics': _relative(_resample_curve(rms, n)),
        'percussive': _relative(_resample_curve(onset, n)),
        'brightness': _norm_array(_resample_curve(centroid, n), *BRIGHTNESS_RANGE),
        'novelty': _relative(_resample_curve(change, n)),
    }

    # A little smoothing: these drive the activation of a whole lobe, and
    # sample-to-sample jitter would read as flicker rather than as musical
    # change. TouchDesigner smooths again downstream.
    width = max(1, int(round(TIMELINE_SMOOTH * TIMELINE_HZ)))
    if width > 1:
        window = np.ones(width) / width
        for name, curve in curves.items():
            curves[name] = np.convolve(np.pad(curve, (width, width), mode='edge'),
                                       window, mode='same')[width:-width]

    return {
        'hz': TIMELINE_HZ,
        'samples': n,
        **{name: [round(float(v), 3) for v in curve] for name, curve in curves.items()},
    }


def _norm_array(values: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)


def waveform(audio: np.ndarray, bars: int = WAVEFORM_BARS) -> list[float]:
    """Peak profile of the track, `bars` values between 0 and 1.

    Peak and not RMS: RMS would flatten to a near-uniform rectangle on
    compressed material, while peaks keep the attacks that make a track's shape
    recognisable. Normalized to the track maximum — it is a map to navigate by,
    not a measure of absolute level.
    """
    if audio.size == 0:
        return []
    # Fewer samples than bars only happens on degenerate files, but without this
    # the reshape would raise and take the whole analysis down.
    bars = min(bars, audio.size)
    # One reshape instead of `bars` slices: a three-minute track is eight
    # million samples and a Python loop would show.
    per_bar = audio.size // bars
    usable = audio[:per_bar * bars].reshape(bars, per_bar)
    peaks = np.abs(usable).max(axis=1)
    top = float(peaks.max())
    if top <= 0:
        return [0.0] * bars
    return [round(float(v / top), 3) for v in peaks]


def analyze(path: Path, progress=None) -> dict:
    """Analyze an audio file. `progress` receives a float 0-1, if given."""
    def step(value: float) -> None:
        if progress is not None:
            progress(value)

    path = Path(path)
    logger.info('Analisi avviata: %s', path.name)

    audio = es.MonoLoader(filename=str(path), sampleRate=SAMPLE_RATE)()
    if audio.size == 0:
        raise ValueError(f'file audio vuoto o non decodificabile: {path.name}')
    duration = float(len(audio) / SAMPLE_RATE)
    step(0.15)

    bpm, beats, beat_confidence, _, _ = es.RhythmExtractor2013(method='multifeature')(audio)
    step(0.45)

    key, scale, key_strength = es.KeyExtractor()(audio)
    step(0.60)

    danceability, _ = es.Danceability()(audio)
    intensity = int(es.Intensity()(audio))          # -1 relaxed, 0 medium, 1 aggressive
    dyn_complexity, loudness_db = es.DynamicComplexity()(audio)
    energy = float(np.sqrt(np.mean(audio ** 2)))    # global RMS
    brightness = float(np.mean(librosa.feature.spectral_centroid(
        y=librosa.resample(audio, orig_sr=SAMPLE_RATE, target_sr=SEGMENT_SR),
        sr=SEGMENT_SR, hop_length=HOP)))
    step(0.75)

    timeline = _timeline(audio, duration)
    step(0.95)

    mood = _mood_from_model(audio)
    if mood is None:
        tempo_n = _norm(float(bpm), *TEMPO_RANGE)
        dance_n = _norm(float(danceability), *DANCEABILITY_RANGE)
        mood = {
            'valence': round(
                VALENCE_WEIGHTS['mode'] * (1.0 if scale == 'major' else 0.0)
                + VALENCE_WEIGHTS['tempo'] * tempo_n
                + VALENCE_WEIGHTS['brightness'] * _norm(brightness, *BRIGHTNESS_RANGE)
                + VALENCE_WEIGHTS['danceability'] * dance_n, 3),
            'arousal': round(
                AROUSAL_WEIGHTS['tempo'] * tempo_n
                + AROUSAL_WEIGHTS['energy'] * _norm(energy, *ENERGY_RANGE)
                + AROUSAL_WEIGHTS['danceability'] * dance_n
                + AROUSAL_WEIGHTS['intensity'] * ((intensity + 1) / 2), 3),
            'source': 'euristica',
        }

    result = {
        'duration': round(duration, 3),
        'waveform': waveform(audio),
        'bpm': round(float(bpm), 2),
        'beat_confidence': round(float(beat_confidence), 3),
        'beats': len(beats),
        'key': key,
        'scale': scale,
        'key_strength': round(float(key_strength), 3),
        'mood': mood,
        'timeline': timeline,
        'descriptors': {
            'danceability': round(float(danceability), 3),
            'intensity': intensity,
            'dynamic_complexity': round(float(dyn_complexity), 3),
            'loudness_db': round(float(loudness_db), 2),
            'energy_rms': round(energy, 4),
            'brightness_hz': round(brightness, 1),
        },
    }
    step(1.0)
    logger.info('Analisi completata: %s — %.1f bpm, %s %s, timeline di %d campioni',
                path.name, result['bpm'], key, scale, timeline['samples'])
    return result
