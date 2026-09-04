"""Split the brain model into one OBJ per anatomical region.

The hemispheres are separate meshes, but the lobes exist only as flat colours
painted on the texture. The colour is read once, offline, and turned into
separate files, so TouchDesigner gets one mesh per region and never samples a
texture at runtime.

Usage:
    python tools/split_lobes.py

See docs/geometry.md for the provenance of the colours and axes.
"""

import json
import os
import sys
from collections import Counter

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbx_reader  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.environ.get('BRAIN_FBX_DIR', '')
FBX = SCRATCH or os.path.join(ROOT, 'touchdesigner', 'exports', 'brain_source',
                              'BrainColourExport.fbx')
TEXTURE = os.path.join(os.path.dirname(FBX), 'Brain_Texture.jpeg')
OUT_DIR = os.path.join(ROOT, 'touchdesigner', 'exports', 'regions')

# Flat texture colours -> lobe. Sampled from the texture and checked against
# the 3D position of the centroids (see docs/geometry.md).
LOBE_COLORS = {
    'frontal':   (52, 166, 157),   # cyan   — the most anterior region
    'parietal':  (223, 220, 64),   # yellow — the most superior
    'temporal':  (52, 181, 56),    # green  — the most lateral and inferior
    'occipital': (195, 43, 42),    # red    — the most posterior
}
# Colours present on the hemispheres but belonging to no lobe: the medial face,
# where the two hemispheres meet. Those points are reassigned to the nearest
# labelled lobe.
NON_LOBE_COLORS = {
    'medial': (222, 177, 148),
    'brown':  (116, 47, 19),
    'white':  (254, 254, 254),
}

# Mesh names in the FBX. Hemispheres are told apart by the sign of X: with
# anterior = +Z and superior = +Y the anatomical frame is right-handed, so the
# subject's right is -X. Note that the two meshes are exact mirrors, so the
# model contains no asymmetry to infer the hemisphere from: this is a consistent
# convention, not a measurement.
HEMISPHERES = {'Brain_Part_06': 'right', 'Brain_Part_04': 'left'}
OTHER_PARTS = {
    'Brain_Part_02': 'cerebellum',
    'Brain_Part_05': 'brainstem',
    'Brain_Part_03': 'other_midline_large',   # probably the corpus callosum
    'Brain_Part_01': 'other_midline_small',   # probably pituitary/pineal
}

# If True the two hemispheres go into the same file: 7 regions instead of 12.
# Nothing is lost — the sagittal plane is exactly X = 0, so the hemisphere stays
# recoverable from the sign of X — and it avoids basing the OSC contract on a
# left/right assignment that is a convention here, not a measurement.
MERGE_HEMISPHERES = True

# Total neurons to share out between the regions. Override without editing the
# file:  BRAIN_TARGET_POINTS=20000 python tools/split_lobes.py
TARGET_POINTS = int(os.environ.get('BRAIN_TARGET_POINTS', '5000'))

# The order of this list defines the lobe_id used by /lobes/activation. Append
# only, never insert: the indices are a contract (docs/protocol.md).
REGION_ORDER_MERGED = [
    'frontal', 'parietal', 'temporal', 'occipital',
    'cerebellum', 'brainstem', 'other_midline',
]
REGION_ORDER_SPLIT = [
    'frontal_left', 'frontal_right',
    'parietal_left', 'parietal_right',
    'temporal_left', 'temporal_right',
    'occipital_left', 'occipital_right',
    'cerebellum', 'brainstem',
    'other_midline_large', 'other_midline_small',
]


def sample_uv_labels(mesh, texture):
    """Label each vertex with the lobe whose colour dominates its UVs.

    UVs are per polygon-vertex, not per vertex: the same vertex can get
    different colours along a boundary, hence the vote.
    """
    width, height = texture.size
    pixels = texture.load()
    flat_uv, uv_index = mesh['uv']

    names = list(LOBE_COLORS) + list(NON_LOBE_COLORS)
    palette = np.array([LOBE_COLORS[n] for n in LOBE_COLORS] +
                       [NON_LOBE_COLORS[n] for n in NON_LOBE_COLORS], dtype=np.int32)

    votes = [Counter() for _ in mesh['vertices']]
    corner = 0
    for poly in mesh['polygons']:
        for vertex_index in poly:
            uv_slot = uv_index[corner] if uv_index else corner
            u = flat_uv[uv_slot * 2]
            v = flat_uv[uv_slot * 2 + 1]
            # The texture origin is top-left, the UV origin bottom-left.
            x = min(width - 1, max(0, int(u * width)))
            y = min(height - 1, max(0, int((1 - v) * height)))
            rgb = np.array(pixels[x, y], dtype=np.int32)
            nearest = int(np.argmin(((palette - rgb) ** 2).sum(axis=1)))
            votes[vertex_index][names[nearest]] += 1
            corner += 1

    labels = []
    for counter in votes:
        lobes = {k: n for k, n in counter.items() if k in LOBE_COLORS}
        labels.append(max(lobes, key=lobes.get) if lobes else None)
    return labels


def fill_unlabeled(vertices, labels):
    """Assign unlabelled vertices (medial face) to the nearest labelled one."""
    pts = np.asarray(vertices, dtype=np.float64)
    known = np.array([i for i, l in enumerate(labels) if l is not None])
    missing = np.array([i for i, l in enumerate(labels) if l is None])
    if len(missing) == 0:
        return labels, 0

    known_pts = pts[known]
    for i in missing:
        d = ((known_pts - pts[i]) ** 2).sum(axis=1)
        labels[i] = labels[known[int(np.argmin(d))]]
    return labels, len(missing)


def write_obj(path, chunks, name):
    """Write an OBJ from one or more chunks (vertices, polygons).

    Several chunks are needed when a region comes from different meshes, e.g. a
    lobe collecting both hemispheres. Each chunk has its own numbering, so the
    vertices are renumbered and concatenated.
    """
    out_vertices, out_faces = [], []
    for vertices, polygons in chunks:
        used = sorted({i for poly in polygons for i in poly})
        base = len(out_vertices) + 1  # OBJ indices start at 1
        remap = {old: base + new for new, old in enumerate(used)}
        out_vertices.extend(vertices[i] for i in used)
        out_faces.extend([remap[i] for i in poly] for poly in polygons)

    with open(path, 'w') as f:
        f.write(f'# BrainViewer — regione: {name}\n')
        f.write('# generato da tools/split_lobes.py, non modificare a mano\n')
        f.write(f'o {name}\n')
        for x, y, z in out_vertices:
            f.write(f'v {x:.6f} {y:.6f} {z:.6f}\n')
        for face in out_faces:
            f.write('f ' + ' '.join(str(i) for i in face) + '\n')
    return len(out_vertices), len(out_faces)


def main():
    if not os.path.exists(FBX):
        sys.exit(f'FBX non trovato: {FBX}\n'
                 'Imposta BRAIN_FBX_DIR col percorso del file, oppure copia\n'
                 'BrainColourExport.fbx e Brain_Texture.jpeg in\n'
                 'touchdesigner/exports/brain_source/')

    version, roots = fbx_reader.parse(FBX)
    meshes = fbx_reader.meshes(roots)
    texture = Image.open(TEXTURE).convert('RGB')
    os.makedirs(OUT_DIR, exist_ok=True)
    # Clear OBJs from previous runs: changing MERGE_HEMISPHERES changes the
    # file names, and leftovers from the old split are confusing.
    for stale in os.listdir(OUT_DIR):
        if stale.endswith('.obj'):
            os.remove(os.path.join(OUT_DIR, stale))
    print(f'FBX v{version}: {len(meshes)} mesh, texture {texture.size[0]}x{texture.size[1]}\n')

    # region -> list of chunks (vertices, polygons) to concatenate in the OBJ
    chunks = {}
    hemispheres_of = {}

    for part, side in HEMISPHERES.items():
        mesh = meshes[part]
        labels = sample_uv_labels(mesh, texture)
        labels, filled = fill_unlabeled(mesh['vertices'], labels)
        print(f'{part} ({side}): {len(mesh["vertices"])} vertici, '
              f'{filled} riassegnati dalla faccia mediale')

        # A face goes to the lobe of its vertices; on a tie at a boundary the
        # first in vote order wins, which is stable between runs.
        by_lobe = {}
        for poly in mesh['polygons']:
            vote = Counter(labels[i] for i in poly)
            by_lobe.setdefault(vote.most_common(1)[0][0], []).append(poly)

        for lobe, polys in sorted(by_lobe.items()):
            region = lobe if MERGE_HEMISPHERES else f'{lobe}_{side}'
            chunks.setdefault(region, []).append((mesh['vertices'], polys))
            hemispheres_of.setdefault(region, set()).add(side)
            print(f'    {lobe:12} -> {region:20} {len(polys):6} facce')

    for part, region in OTHER_PARTS.items():
        # Cerebellum and brainstem are already single midline structures and
        # stay separate; only the two minor midline parts are merged, being too
        # small to deserve an activation channel each.
        if MERGE_HEMISPHERES and region.startswith('other_midline'):
            region = 'other_midline'
        mesh = meshes[part]
        chunks.setdefault(region, []).append((mesh['vertices'], mesh['polygons']))
        hemispheres_of.setdefault(region, set()).add('midline')
        print(f'{part} -> {region}: {len(mesh["polygons"])} facce')

    manifest = []
    for region, region_chunks in chunks.items():
        path = os.path.join(OUT_DIR, f'{region}.obj')
        nv, nf = write_obj(path, region_chunks, region)
        sides = hemispheres_of[region]
        manifest.append({
            'region': region,
            'hemisphere': 'both' if sides == {'left', 'right'} else sorted(sides)[0],
            'vertices': nv, 'faces': nf, 'file': f'{region}.obj',
        })

    region_order = REGION_ORDER_MERGED if MERGE_HEMISPHERES else REGION_ORDER_SPLIT
    order = {name: i for i, name in enumerate(region_order)}
    manifest.sort(key=lambda e: order.get(e['region'], 999))
    for i, entry in enumerate(manifest):
        entry['lobe_id'] = i

    # Neurons are shared out proportionally to the face count (a proxy for
    # surface area), so on-screen density stays uniform instead of crowding the
    # small regions. The share_percent column allows recomputing any other total
    # by hand, without rerunning the script.
    total_faces = sum(e['faces'] for e in manifest)
    for entry in manifest:
        entry['share_percent'] = round(entry['faces'] / total_faces * 100, 2)

    # Largest remainder method: rounding each one independently would never add
    # up to the exact total.
    exact = [e['faces'] / total_faces * TARGET_POINTS for e in manifest]
    counts = [int(x) for x in exact]
    leftover = TARGET_POINTS - sum(counts)
    for i in sorted(range(len(manifest)), key=lambda i: exact[i] - counts[i], reverse=True)[:leftover]:
        counts[i] += 1
    for entry, n in zip(manifest, counts):
        entry['suggested_points'] = n

    with open(os.path.join(OUT_DIR, 'regions.json'), 'w') as f:
        json.dump({'source': os.path.basename(FBX),
                   'note': "lobe_id e' l'indice del canale in /lobes/activation",
                   'regions': manifest}, f, indent=2)

    # Same table as CSV: TouchDesigner loads it into a Table DAT without code.
    with open(os.path.join(OUT_DIR, 'regions.csv'), 'w') as f:
        cols = ['lobe_id', 'region', 'file', 'suggested_points', 'share_percent',
                'vertices', 'faces']
        f.write('\t'.join(cols) + '\n')
        for entry in manifest:
            f.write('\t'.join(str(entry[c]) for c in cols) + '\n')

    total_v = sum(e['vertices'] for e in manifest)
    total_f = sum(e['faces'] for e in manifest)
    print(f'\n{len(manifest)} regioni, {total_v} vertici, {total_f} facce -> {OUT_DIR}')


if __name__ == '__main__':
    main()
