"""Check the webcam and unlock the macOS permission. Run once.

On macOS the camera permission prompt must come from the main thread. The
backend opens the webcam in a worker thread, where the request fails with
"OpenCV: can not spin main run loop from other thread". This script opens it
from the main thread, so the prompt appears and the permission is granted once.

Usage:
    conda activate brain_viewer
    python tools/check_camera.py
"""

import sys
import time

import cv2


def main(index: int = 0) -> int:
    print(f'Apertura webcam {index} dal thread principale...')
    capture = cv2.VideoCapture(index)
    if not capture.isOpened():
        print('\nNON APERTA.\n'
              'Se e comparsa la richiesta di permesso, concedila e rilancia.\n'
              'Altrimenti vai in Impostazioni di Sistema > Privacy e sicurezza >\n'
              'Fotocamera e abilita il terminale che stai usando, poi rilancia.\n'
              'Se hai piu di una camera, prova: python tools/check_camera.py 1')
        return 1

    ok, frame = capture.read()
    if not ok:
        print('Camera aperta ma nessun fotogramma letto: e occupata da un altro programma?')
        capture.release()
        return 1

    height, width = frame.shape[:2]
    print(f'OK — fotogrammi {width}x{height}')

    # Measure the real frame rate: the one the driver reports often lies.
    start, frames = time.monotonic(), 0
    while time.monotonic() - start < 2.0:
        if capture.read()[0]:
            frames += 1
    print(f'circa {frames / 2.0:.0f} fps')
    capture.release()

    try:
        import mediapipe as mp
        import numpy as np
    except ImportError as exc:
        print(f'MediaPipe non disponibile ({exc}): il pose tracking non funzionera.')
        return 1

    print('Verifica di MediaPipe sul fotogramma acquisito...')
    with mp.solutions.pose.Pose(model_complexity=0) as pose:
        result = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    if result.pose_landmarks:
        visible = sum(1 for lm in result.pose_landmarks.landmark if lm.visibility > 0.5)
        print(f'Persona rilevata: {visible} landmark ben visibili su 33.')
    else:
        print('Nessuna persona nel fotogramma — normale se non eri inquadrato.\n'
              'La catena funziona lo stesso: mettiti davanti e riprova.')

    print('\nTutto pronto: ora il backend puo aprire la webcam.\n'
          '  curl -X POST http://localhost:8000/api/pose/start')
    return 0


if __name__ == '__main__':
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
