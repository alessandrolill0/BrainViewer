"""Offline track analysis: rhythm, key, structure, mood.

Runs once per track after upload; the result is stored in the library. Real-time
analysis is a separate concern and lives in TouchDesigner.

Essentia does the signal work, librosa only the structural segmentation. The
audio is decoded once and passed to both.

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


def _sections(audio: np.ndarray, beats: np.ndarray, duration: float) -> list[dict]:
    """Split the track into sections and try to name them.

    The criterion is timbre: beat-synchronous MFCCs are compared and cut at the
    points of change. The names are a heuristic labelling, not recognition.
    """
    if duration < 20 or len(beats) < 8:
        # Too short to have a structure: one section is the honest answer.
        return [{'start': 0.0, 'end': round(duration, 3), 'label': 'verse'}]

    y = librosa.resample(audio, orig_sr=SAMPLE_RATE, target_sr=SEGMENT_SR)
    mfcc = librosa.feature.mfcc(y=y, sr=SEGMENT_SR, n_mfcc=13, hop_length=HOP)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]

    beat_frames = librosa.time_to_frames(beats, sr=SEGMENT_SR, hop_length=HOP)
    beat_frames = np.unique(np.clip(beat_frames, 0, mfcc.shape[1] - 1))
    if len(beat_frames) < 4:
        return [{'start': 0.0, 'end': round(duration, 3), 'label': 'verse'}]

    sync = librosa.util.sync(mfcc, beat_frames, aggregate=np.mean)
    sync_rms = librosa.util.sync(rms[np.newaxis, :], beat_frames, aggregate=np.mean)[0]

    # Roughly one section every 25 seconds, within reasonable bounds.
    n_segments = int(min(10, max(3, duration // 25)))
    n_segments = min(n_segments, sync.shape[1] - 1)
    bounds = librosa.segment.agglomerative(sync, n_segments)
    bounds = np.unique(np.concatenate([[0], bounds, [sync.shape[1]]]))

    beat_times = librosa.frames_to_time(beat_frames, sr=SEGMENT_SR, hop_length=HOP)
    segments = []
    for start_idx, end_idx in zip(bounds[:-1], bounds[1:]):
        if end_idx <= start_idx:
            continue
        start = float(beat_times[min(start_idx, len(beat_times) - 1)])
        end = float(beat_times[min(end_idx, len(beat_times) - 1)]) if end_idx < len(beat_times) else duration
        segments.append({
            'start': round(start, 3),
            'end': round(max(end, start + 0.1), 3),
            '_timbre': sync[:, start_idx:end_idx].mean(axis=1),
            '_energy': float(sync_rms[start_idx:end_idx].mean()),
        })
    if not segments:
        return [{'start': 0.0, 'end': round(duration, 3), 'label': 'verse'}]

    segments[-1]['end'] = round(duration, 3)
    _label_sections(segments)

    # Energy relative to the other sections, 0-1: it tells the mapping whether
    # this chorus pushes harder than this verse, within this track.
    energies = np.array([s['_energy'] for s in segments])
    lo, hi = float(energies.min()), float(energies.max())
    for seg, value in zip(segments, energies):
        seg['energy'] = round(float((value - lo) / (hi - lo)) if hi > lo else 0.5, 3)

    return [{k: v for k, v in s.items() if not k.startswith('_')} for s in segments]


def _label_sections(segments: list[dict]) -> None:
    """Assign intro/verse/chorus/bridge/outro from similarity and energy."""
    for seg in segments:
        seg['label'] = 'verse'

    if len(segments) > 2:
        from sklearn.cluster import AgglomerativeClustering  # ships with librosa

        timbres = np.vstack([s['_timbre'] for s in segments])
        n_clusters = min(3, len(segments))
        labels = AgglomerativeClustering(n_clusters=n_clusters).fit_predict(timbres)

        # Chorus: among the groups occurring at least twice, the one with the
        # highest mean energy. If none repeats, the track has no chorus.
        energies, counts = {}, {}
        for label, seg in zip(labels, segments):
            energies.setdefault(label, []).append(seg['_energy'])
            counts[label] = counts.get(label, 0) + 1
        repeated = [l for l, n in counts.items() if n >= 2]
        if repeated:
            chorus = max(repeated, key=lambda l: np.mean(energies[l]))
            for label, seg in zip(labels, segments):
                if label == chorus:
                    seg['label'] = 'chorus'

        # A group appearing once, in the middle of the track, is a bridge.
        for i, (label, seg) in enumerate(zip(labels, segments)):
            if counts[label] == 1 and 0 < i < len(segments) - 1 and seg['label'] != 'chorus':
                seg['label'] = 'bridge'

    # Intro and outro only if genuinely short relative to the track: a first
    # section can perfectly well already be a verse.
    total = segments[-1]['end'] - segments[0]['start']
    max_edge = max(25.0, total * 0.15)
    if segments[0]['end'] - segments[0]['start'] <= max_edge:
        segments[0]['label'] = 'intro'
    if len(segments) > 1 and segments[-1]['end'] - segments[-1]['start'] <= max_edge:
        segments[-1]['label'] = 'outro'


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

    sections = _sections(audio, np.asarray(beats), duration)
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
        'sections': sections,
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
    logger.info('Analisi completata: %s — %.1f bpm, %s %s, %d sezioni',
                path.name, result['bpm'], key, scale, len(sections))
    return result
