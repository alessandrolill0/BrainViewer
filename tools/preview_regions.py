"""Generate a PNG preview of the separated regions, for a visual check.

Plain orthographic rendering with the painter's algorithm: it does not need to
look good, it needs to show at a glance if a lobe ended up in the wrong place.

Usage:
    python tools/preview_regions.py
"""

import json
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGIONS = os.path.join(ROOT, 'touchdesigner', 'exports', 'regions')
OUT = os.path.join(REGIONS, 'preview.png')

COLORS = {
    'frontal':   (52, 166, 157),
    'parietal':  (223, 220, 64),
    'temporal':  (52, 181, 56),
    'occipital': (195, 43, 42),
    'cerebellum': (150, 90, 200),
    'brainstem':  (230, 140, 60),
    'other_midline': (120, 120, 120),
}

SIZE = 620
# (title, horizontal axis, vertical axis, depth axis, farthest first)
# The subject's right is -X, so a camera at -X looks at their right side and in
# the top/front views their right falls on the left of the image, as when
# looking at someone face to face.
VIEWS = [
    ('laterale destra  (fronte a destra)',              2, 1, 0, True),
    ('dall alto  (fronte in alto, destra a sinistra)',  0, 2, 1, False),
    ('frontale  (destra del soggetto a sinistra)',      0, 1, 2, False),
]


def load_obj(path):
    verts, faces = [], []
    for line in open(path):
        if line.startswith('v '):
            verts.append(tuple(float(v) for v in line.split()[1:4]))
        elif line.startswith('f '):
            faces.append([int(p.split('/')[0]) - 1 for p in line.split()[1:]])
    return verts, faces


def main():
    manifest = json.load(open(os.path.join(REGIONS, 'regions.json')))['regions']
    loaded = []
    for entry in manifest:
        verts, faces = load_obj(os.path.join(REGIONS, entry['file']))
        loaded.append((entry, verts, faces))

    all_pts = [p for _, verts, _ in loaded for p in verts]
    lo = [min(p[i] for p in all_pts) for i in range(3)]
    hi = [max(p[i] for p in all_pts) for i in range(3)]
    span = max(hi[i] - lo[i] for i in range(3))
    mid = [(hi[i] + lo[i]) / 2 for i in range(3)]

    canvas = Image.new('RGB', (SIZE * len(VIEWS), SIZE + 28), (16, 16, 20))
    draw = ImageDraw.Draw(canvas)

    for col, (title, h_ax, v_ax, d_ax, far_first) in enumerate(VIEWS):
        ox = col * SIZE
        scale = SIZE * 0.86 / span

        def project(p):
            x = ox + SIZE / 2 + (p[h_ax] - mid[h_ax]) * scale
            y = 28 + SIZE / 2 - (p[v_ax] - mid[v_ax]) * scale
            return x, y

        polys = []
        for entry, verts, faces in loaded:
            base = COLORS.get(entry['region'].split('_')[0], (200, 200, 200))
            if entry['region'].startswith('other'):
                base = COLORS['other_midline']
            dark = tuple(int(c * 0.62) for c in base)
            for face in faces:
                pts = [verts[i] for i in face]
                depth = sum(p[d_ax] for p in pts) / len(pts)
                # The hemisphere is no longer a separate file but stays
                # readable from the sign of X: here only to tell them apart.
                cx = sum(p[0] for p in pts) / len(pts)
                polys.append((depth, dark if cx > 0 else base, [project(p) for p in pts]))

        polys.sort(key=lambda t: t[0], reverse=far_first)
        for _, color, pts in polys:
            draw.polygon(pts, fill=color)

        draw.text((ox + 12, 8), title, fill=(210, 210, 210))
        if col:
            draw.line([(ox, 0), (ox, SIZE + 28)], fill=(70, 70, 80))

    canvas.save(OUT)
    print(f'anteprima salvata: {OUT}')


if __name__ == '__main__':
    main()
