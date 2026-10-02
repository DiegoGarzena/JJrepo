# -*- coding: utf-8 -*-
"""
Pure-Python helpers for the "Points Along 3D Line" plugin.

Nothing in this module imports QGIS, so the whole geometric logic can be unit
tested with a plain Python interpreter (see ``tests/test_core.py``).

Pipeline used by the processing algorithm:

    line vertices (x, y)
        -> densify_xy()          add intermediate vertices
        -> GridSampler.z_at()    elevation from the DTM (bilinear interpolation)
        -> drape_vertices()      attach Z, handle vertices outside the DTM
        -> resample_3d()         walk the 3D polyline, emit a point every
                                 ``interval`` metres of 3D path length
        -> build_records()       attributes (distances, slope, azimuth ...)
"""
import math
from collections import OrderedDict

# Absolute tolerance (in map units) used when comparing path distances.
EPS = 1e-9
# Remaining path shorter than this is not worth an extra "end point".
END_TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# Densification
# ---------------------------------------------------------------------------
def densify_xy(vertices, step):
    """Yield the (x, y) vertices with extra points so that no segment is
    longer than ``step``. Original vertices are always preserved."""
    it = iter(vertices)
    try:
        x1, y1 = next(it)
    except StopIteration:
        return
    yield (x1, y1)
    for x2, y2 in it:
        dx = x2 - x1
        dy = y2 - y1
        if step > 0:
            parts = int(math.ceil(math.hypot(dx, dy) / step - EPS))
        else:
            parts = 1
        for k in range(1, parts):
            t = k / parts
            yield (x1 + t * dx, y1 + t * dy)
        yield (x2, y2)
        x1, y1 = x2, y2


# ---------------------------------------------------------------------------
# Raster sampling
# ---------------------------------------------------------------------------
def bilinear(col_f, row_f, get_value):
    """Bilinear interpolation on a grid of pixel centres.

    ``col_f`` / ``row_f`` are fractional pixel-centre indices (pixel centre
    of column 0 is 0.0). ``get_value(col, row)`` returns the pixel value or
    ``None`` (NoData / outside the raster). NoData neighbours are ignored and
    the remaining weights are renormalised; ``None`` is returned only when no
    neighbour is valid.
    """
    c0 = math.floor(col_f)
    r0 = math.floor(row_f)
    tx = col_f - c0
    ty = row_f - r0
    acc = 0.0
    wsum = 0.0
    for dc, dr, w in (
        (0, 0, (1.0 - tx) * (1.0 - ty)),
        (1, 0, tx * (1.0 - ty)),
        (0, 1, (1.0 - tx) * ty),
        (1, 1, tx * ty),
    ):
        if w <= 0.0:
            continue
        v = get_value(c0 + dc, r0 + dr)
        if v is None:
            continue
        acc += w * v
        wsum += w
    if wsum <= 1e-12:
        return None
    return acc / wsum


class GridSampler:
    """Bilinear sampler for a north-up raster, reading it tile by tile.

    ``read_tile(col0, row0, ncols, nrows)`` must return a callable
    ``getter(row, col) -> float | None`` (indices relative to the tile).
    Tiles are cached (LRU) so that consecutive points along a line do not
    hit the raster provider again and again.
    """

    def __init__(self, xmin, ymax, px, py, width, height, read_tile,
                 tile_size=128, max_tiles=64):
        self.xmin = xmin
        self.ymax = ymax
        self.px = px
        self.py = py
        self.width = width
        self.height = height
        self.xmax = xmin + width * px
        self.ymin = ymax - height * py
        self._read_tile = read_tile
        self._tile_size = tile_size
        self._max_tiles = max_tiles
        self._cache = OrderedDict()

    def _value(self, col, row):
        if col < 0 or row < 0 or col >= self.width or row >= self.height:
            return None
        t = self._tile_size
        key = (col // t, row // t)
        entry = self._cache.get(key)
        if entry is None:
            c0 = key[0] * t
            r0 = key[1] * t
            getter = self._read_tile(c0, r0,
                                     min(t, self.width - c0),
                                     min(t, self.height - r0))
            entry = (c0, r0, getter)
            self._cache[key] = entry
            if len(self._cache) > self._max_tiles:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        c0, r0, getter = entry
        return getter(row - r0, col - c0)

    def z_at(self, x, y):
        """Interpolated value at (x, y), or None outside the raster/NoData."""
        if x < self.xmin or x > self.xmax or y < self.ymin or y > self.ymax:
            return None
        col_f = (x - self.xmin) / self.px - 0.5
        row_f = (self.ymax - y) / self.py - 0.5
        return bilinear(col_f, row_f, self._value)


# ---------------------------------------------------------------------------
# Draping on the DTM
# ---------------------------------------------------------------------------
def drape_vertices(xy_points, z_at, extend=False, is_canceled=None):
    """Sample the DTM at every (x, y) vertex.

    Vertices without a DTM value (outside the raster or on NoData) are
    handled as follows:

    * in the middle of the line (valid values before and after): they are
      discarded, so the elevation is interpolated linearly across the gap;
    * at the ends of the line (before the first / after the last valid
      value): discarded, or, when ``extend`` is True, kept with the
      elevation of the nearest valid vertex (flat extension).

    Returns ``(points, valid_range, n_total, n_missing)`` where ``points`` is
    a list of (x, y, z), ``valid_range`` is the (start, end) 2D path distance
    covered by vertices whose elevation really comes from the DTM (None if
    there is none), ``n_total`` the number of sampled vertices and
    ``n_missing`` how many of them had no DTM value.
    """
    points = []
    real = []          # True where the elevation really comes from the DTM
    gap = []           # consecutive vertices without DTM value (extend only)
    last_z = None
    n_total = n_missing = 0

    for n, (x, y) in enumerate(xy_points):
        if is_canceled is not None and n % 50000 == 0 and is_canceled():
            break
        n_total += 1
        z = z_at(x, y)
        if z is None:
            n_missing += 1
            if extend:
                gap.append((x, y))
            continue
        if gap and last_z is None:
            # vertices before the DTM: flat extension of the first value
            for gx, gy in gap:
                points.append((gx, gy, z))
                real.append(False)
        gap = []       # a gap in the middle of the line is just bridged
        points.append((x, y, z))
        real.append(True)
        last_z = z

    if last_z is None:
        return [], None, n_total, n_missing

    if gap:
        # vertices after the DTM: flat extension of the last value
        for gx, gy in gap:
            points.append((gx, gy, last_z))
            real.append(False)

    first = last = None
    cum = 0.0
    for i, (x, y, _z) in enumerate(points):
        if i:
            cum += math.hypot(x - points[i - 1][0], y - points[i - 1][1])
        if real[i]:
            if first is None:
                first = cum
            last = cum
    return points, (first, last), n_total, n_missing


# ---------------------------------------------------------------------------
# Resampling along a 3D polyline
# ---------------------------------------------------------------------------
def resample_3d(points, interval, start_offset=0.0, include_end=False):
    """Walk a 3D polyline and yield a point every ``interval`` of 3D path
    length, starting at ``start_offset`` from the first vertex.

    ``points`` is an iterable of (x, y, z). Yields tuples
    ``(x, y, z, dist_2d_from_start, dist_3d_from_start)``; both distances are
    measured *along the path*. With ``include_end`` the last vertex is also
    emitted when it does not coincide with the last regular point.
    """
    if interval <= 0:
        raise ValueError("interval must be > 0")
    if start_offset < 0:
        raise ValueError("start_offset must be >= 0")

    it = iter(points)
    try:
        px, py, pz = next(it)
    except StopIteration:
        return

    cum2 = 0.0
    cum3 = 0.0
    k = 0
    target = start_offset
    last_target = None

    for x, y, z in it:
        dx = x - px
        dy = y - py
        dz = z - pz
        seg2 = math.hypot(dx, dy)
        seg3 = math.sqrt(seg2 * seg2 + dz * dz)
        if seg3 == 0.0:
            continue
        end3 = cum3 + seg3
        while target <= end3 + EPS:
            ratio = min(1.0, max(0.0, (target - cum3) / seg3))
            yield (px + ratio * dx, py + ratio * dy, pz + ratio * dz,
                   cum2 + ratio * seg2, target)
            last_target = target
            k += 1
            target = start_offset + k * interval
        cum2 += seg2
        cum3 = end3
        px, py, pz = x, y, z

    if include_end and last_target is not None \
            and cum3 - last_target > END_TOLERANCE:
        yield (px, py, pz, cum2, cum3)


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------
def azimuth_deg(dx, dy):
    """Grid bearing in degrees, 0 = +Y (north), clockwise, range [0, 360)."""
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def build_records(samples):
    """Turn the output of :func:`resample_3d` (as a list) into attribute
    dictionaries.

    * ``dist_*_p`` / ``dist_*_tot``: distances along the path, from the
      previous point / from the start of the line.
    * ``delta_z_p``: elevation difference with the previous point.
    * ``slope_*``: signed (positive uphill, negative downhill), computed as
      ``delta_z_p / dist_2d_p``.
    * ``azimuth_deg``: bearing of the chord previous point -> this point (for
      the first point: towards the next point).
    First-point values that need a previous point are 0 (distances, delta z)
    or None (slope).
    """
    records = []
    n = len(samples)
    for i, (x, y, z, d2, d3) in enumerate(samples):
        rec = {
            'seq_id': i + 1,
            'x': x, 'y': y, 'z': z,
            'dist_2d_tot': d2, 'dist_3d_tot': d3,
        }
        if i == 0:
            rec.update(dist_2d_p=0.0, dist_3d_p=0.0, delta_z_p=0.0,
                       slope_deg=None, slope_pct=None)
            if n > 1:
                dx = samples[1][0] - x
                dy = samples[1][1] - y
            else:
                dx = dy = 0.0
        else:
            ppx, ppy, ppz, pd2, pd3 = samples[i - 1]
            d2p = d2 - pd2
            dz = z - ppz
            rec.update(dist_2d_p=d2p, dist_3d_p=d3 - pd3, delta_z_p=dz)
            if d2p > 1e-12:
                rec['slope_deg'] = math.degrees(math.atan2(dz, d2p))
                rec['slope_pct'] = dz / d2p * 100.0
            else:  # purely vertical step
                rec['slope_deg'] = math.copysign(90.0, dz) if dz else 0.0
                rec['slope_pct'] = None
            dx = x - ppx
            dy = y - ppy
        if math.hypot(dx, dy) > 1e-12:
            rec['azimuth_deg'] = azimuth_deg(dx, dy)
        else:
            rec['azimuth_deg'] = None
        records.append(rec)
    return records
