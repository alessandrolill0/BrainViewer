"""Minimal binary FBX reader (Kaydara, version 7500+).

Only used to extract geometry and UVs from the brain model: not a complete FBX
parser and not meant to be one.
"""

import struct
import zlib


class _Reader:
    def __init__(self, buf):
        self.b = buf
        self.p = 0

    def take(self, fmt, size):
        value = struct.unpack_from(fmt, self.b, self.p)[0]
        self.p += size
        return value


def _read_property(r):
    kind = chr(r.b[r.p])
    r.p += 1
    scalars = {'Y': ('<h', 2), 'C': ('<b', 1), 'I': ('<i', 4),
               'F': ('<f', 4), 'D': ('<d', 8), 'L': ('<q', 8)}
    if kind in scalars:
        fmt, size = scalars[kind]
        return r.take(fmt, size)

    if kind in 'fdlib':
        count = r.take('<I', 4)
        encoding = r.take('<I', 4)
        packed_len = r.take('<I', 4)
        raw = r.b[r.p:r.p + packed_len]
        r.p += packed_len
        if encoding == 1:  # i grandi array sono deflate-compressi
            raw = zlib.decompress(raw)
        code = {'f': 'f', 'd': 'd', 'l': 'q', 'i': 'i', 'b': 'b'}[kind]
        return list(struct.unpack('<%d%s' % (count, code), raw))

    if kind in 'SR':
        length = r.take('<I', 4)
        raw = r.b[r.p:r.p + length]
        r.p += length
        return raw.decode('utf-8', 'replace') if kind == 'S' else raw

    raise ValueError('tipo di proprieta FBX sconosciuto: %r' % kind)


def _read_node(r, wide):
    size, fmt = (8, '<Q') if wide else (4, '<I')
    end = r.take(fmt, size)
    n_props = r.take(fmt, size)
    r.take(fmt, size)  # lunghezza della lista proprieta, non serve
    name_len = r.take('<B', 1)
    name = r.b[r.p:r.p + name_len].decode('utf-8', 'replace')
    r.p += name_len

    if end == 0:  # record nullo: fine della lista di figli
        return None

    props = [_read_property(r) for _ in range(n_props)]
    children = []
    while r.p < end:
        child = _read_node(r, wide)
        if child is None:
            break
        children.append(child)
    r.p = end
    return Node(name, props, children)


class Node:
    __slots__ = ('name', 'props', 'children')

    def __init__(self, name, props, children):
        self.name = name
        self.props = props
        self.children = children

    def child(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def children_named(self, name):
        return [c for c in self.children if c.name == name]

    def __repr__(self):
        return f'<Node {self.name} props={len(self.props)} kids={len(self.children)}>'


def parse(path):
    """Return the list of top-level nodes of the FBX."""
    data = open(path, 'rb').read()
    if not data.startswith(b'Kaydara FBX Binary'):
        raise ValueError(f'{path} non e un FBX binario')
    version = struct.unpack_from('<I', data, 23)[0]
    r = _Reader(data)
    r.p = 27
    wide = version >= 7500

    roots = []
    while r.p < len(data) - 13:
        node = _read_node(r, wide)
        if node is None:
            break
        roots.append(node)
    return version, roots


def strip_name(raw):
    """FBX names are 'name\\x00\\x01Type': keep only the name."""
    return raw.split('\x00\x01')[0] if isinstance(raw, str) else raw


def meshes(roots):
    """Extract meshes as a dict: Model name -> {vertices, polygons, uv}.

    Geometry nodes carry no readable name — that lives on the connected Model —
    so it has to be reconstructed through the Connections.
    """
    objects = [n for root in roots if root.name == 'Objects' for n in root.children]
    connections = [n for root in roots if root.name == 'Connections' for n in root.children]

    models = {o.props[0]: strip_name(o.props[1])
              for o in objects if o.name == 'Model'}
    geometries = {o.props[0]: o for o in objects if o.name == 'Geometry'}

    geo_to_model = {}
    for c in connections:
        if len(c.props) >= 3 and c.props[1] in geometries and c.props[2] in models:
            geo_to_model[c.props[1]] = models[c.props[2]]

    out = {}
    for geo_id, geo in geometries.items():
        flat = geo.child('Vertices').props[0]
        verts = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)]

        # PolygonVertexIndex: the last index of each polygon is negated (~i)
        # to mark the end of the face.
        polys, current = [], []
        for idx in geo.child('PolygonVertexIndex').props[0]:
            if idx < 0:
                current.append(~idx)
                polys.append(current)
                current = []
            else:
                current.append(idx)

        uvs = None
        layer = geo.child('LayerElementUV')  # se ce n'e' piu' di uno, il primo basta
        if layer is not None:
            flat_uv = layer.child('UV').props[0]
            uv_index = layer.child('UVIndex')
            uv_index = uv_index.props[0] if uv_index else None
            uvs = (flat_uv, uv_index)

        out[geo_to_model.get(geo_id, f'geometry_{geo_id}')] = {
            'vertices': verts,
            'polygons': polys,
            'uv': uvs,
        }
    return out
