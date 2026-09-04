#!/usr/bin/env python3
"""Measure what Face Landmarker costs on this machine, before adopting it.

Full body landmarks were dropped because they only reached 16 fps, and Face
Landmarker was the candidate to replace face_detection in pose.py. If it cannot
keep up with the webcam rate the plan changes shape, so it is worth knowing
first. Compares the two trackers on the same video stream and prints the times.

    conda activate brain_viewer
    python tools/download_models.py     # fetches face_landmarker.task
    python tools/bench_face.py
    python tools/bench_face.py --frames 200 --width 640

Opens the webcam from the main thread, like tools/check_camera.py: on macOS the
permission prompt cannot come from a worker thread.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

MODEL = Path(__file__).resolve().parents[1] / 'backend' / 'models' / 'face_landmarker.task'

#: The facial actions of interest, out of the 52 available: the observable,
#: nameable ones the panel can show without interpreting.
AZIONI = ['mouthSmileLeft', 'mouthSmileRight', 'browInnerUp',
          'browDownLeft', 'browDownRight', 'jawOpen', 'eyeBlinkLeft']


def riepilogo(nome: str, tempi: list[float]) -> None:
    if not tempi:
        print(f'{nome:<22} nessun campione')
        return
    medio = statistics.mean(tempi)
    p95 = sorted(tempi)[int(len(tempi) * 0.95) - 1]
    print(f'{nome:<22} medio {medio * 1000:6.1f} ms   p95 {p95 * 1000:6.1f} ms   '
          f'-> {1 / medio:5.1f} fps sostenibili')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=int, default=120)
    parser.add_argument('--width', type=int, default=640,
                        help='larghezza del fotogramma: il costo scala con i pixel')
    args = parser.parse_args()

    if not MODEL.exists():
        sys.exit(f'manca {MODEL.name}: esegui prima python tools/download_models.py')

    cam = cv2.VideoCapture(0)
    if not cam.isOpened():
        sys.exit('webcam non disponibile. Se e\' la prima volta, esegui '
                 'python tools/check_camera.py per concedere il permesso.')
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, int(args.width * 9 / 16))

    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(MODEL)),
        # The blendshapes are the reason for this model: without the flag only
        # the 478 points come back.
        output_face_blendshapes=True,
        num_faces=1,
    ))
    detector = mp.solutions.face_detection.FaceDetection(model_selection=0,
                                                         min_detection_confidence=0.5)

    t_land, t_det, visti = [], [], 0
    ultima = {}
    print(f'misuro su {args.frames} fotogrammi a {args.width}px di larghezza...\n')
    for _ in range(args.frames):
        ok, frame = cam.read()
        if not ok:
            continue
        frame = cv2.flip(frame, 1)                      # come nel backend
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        t0 = time.perf_counter()
        detector.process(rgb)
        t_det.append(time.perf_counter() - t0)

        immagine = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        t0 = time.perf_counter()
        risultato = landmarker.detect(immagine)
        t_land.append(time.perf_counter() - t0)

        if risultato.face_blendshapes:
            visti += 1
            ultima = {c.category_name: c.score for c in risultato.face_blendshapes[0]}

    cam.release()

    print()
    riepilogo('face_detection', t_det)
    riepilogo('face_landmarker', t_land)
    riepilogo('i due insieme', [a + b for a, b in zip(t_det, t_land)])
    print(f'\nvolto rilevato in {visti}/{len(t_land)} fotogrammi')

    if ultima:
        print('\nazioni facciali nell\'ultimo fotogramma:')
        for nome in AZIONI:
            print(f'  {nome:<18} {ultima.get(nome, 0.0):.3f}')
        print(f'  ({len(ultima)} blendshape disponibili in totale)')

    if t_land:
        fps = 1 / statistics.mean(t_land)
        print()
        if fps >= 25:
            print('VERDETTO: regge il rate della webcam — puo\' sostituire face_detection.')
        elif fps >= 10:
            print('VERDETTO: non regge i 30 Hz della posa. Serve tenere face_detection '
                  'per la posa e far girare l\'espressione piu\' lentamente, o ridurre '
                  'la risoluzione (riprova con --width 480).')
        else:
            print('VERDETTO: troppo lento su questa macchina. Da rivedere.')


if __name__ == '__main__':
    main()
