"""Export the list of operators of the TouchDesigner project to a CSV.

Usage: see touchdesigner/README.md. In short, open the Textport in TD
(Dialogs > Textport and DATs) and paste:

    exec(open('/Users/alessandrolillo/Developer/BrainViewer/touchdesigner/export_ops.py').read())

Writes touchdesigner/exports/operators.csv.
"""

import csv
import os

# Where to start scanning. '/project1' is the default container of a new
# project: change this path if the brain lives elsewhere.
START = '/project1'

# The .toe lives in touchdesigner/, so exports/ sits next to it. Falls back to
# an absolute path if the project has not been saved yet.
folder = project.folder or '/Users/alessandrolillo/Developer/BrainViewer/touchdesigner'
out_dir = os.path.join(folder, 'exports')
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, 'operators.csv')

start_op = op(START)
if start_op is None:
    raise RuntimeError(
        f"Nessun operatore a {START}. Apri il Textport, scrivi  op('/').children  "
        f"per vedere i contenitori disponibili e correggi START in questo script."
    )

rows = []
for o in [start_op] + list(start_op.findChildren(depth=None)):
    info = {
        'path': o.path,
        'name': o.name,
        'family': o.family,          # SOP, TOP, CHOP, DAT, COMP, MAT
        'type': o.OPType,
        'n_children': len(o.children),
        'points': '',
        'primitives': '',
        'channels': '',
        'resolution': '',
        'comment': (o.comment or '').replace('\n', ' '),
    }

    # Details that only exist for some families: reading them on others raises,
    # hence the guard.
    try:
        if o.family == 'SOP':
            info['points'] = len(o.points)
            info['primitives'] = len(o.prims)
        elif o.family == 'CHOP':
            info['channels'] = ' '.join(c.name for c in o.chans())
        elif o.family == 'TOP':
            info['resolution'] = f'{o.width}x{o.height}'
    except Exception as exc:
        info['comment'] = f'{info["comment"]} [errore lettura: {exc}]'.strip()

    rows.append(info)

with open(out_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

print(f'Esportati {len(rows)} operatori in {out_path}')
