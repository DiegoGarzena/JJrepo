# -*- coding: utf-8 -*-
"""
Points Along 3D Line - QGIS Processing provider and algorithm.

The geometric work (densification, bilinear DTM sampling, 3D resampling,
attribute computation) lives in ``core.py``; this module only connects it to
the QGIS Processing framework.
"""
import math

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    NULL,
    Qgis,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBand,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterDistance,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingProvider,
    QgsRectangle,
    QgsWkbTypes,
)

from .core import (GridSampler, build_records, densify_xy, drape_vertices,
                   resample_3d)

# --- QGIS version compatibility (plugin supports 3.22 -> 3.99) -------------
if Qgis.QGIS_VERSION_INT >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    _T_STRING = QMetaType.Type.QString
    _T_INT = QMetaType.Type.Int
    _T_DOUBLE = QMetaType.Type.Double
else:
    _T_STRING = QVariant.String
    _T_INT = QVariant.Int
    _T_DOUBLE = QVariant.Double

if Qgis.QGIS_VERSION_INT >= 33000:
    _POINT_Z = Qgis.WkbType.PointZ
else:
    _POINT_Z = QgsWkbTypes.PointZ

if Qgis.QGIS_VERSION_INT >= 33600:
    _SOURCE_LINE = Qgis.ProcessingSourceType.VectorLine
else:
    _SOURCE_LINE = QgsProcessing.TypeVectorLine

try:
    _FLAG_ADVANCED = Qgis.ProcessingParameterFlag.Advanced
except AttributeError:
    _FLAG_ADVANCED = QgsProcessingParameterDefinition.FlagAdvanced

HOME_URL = 'https://github.com/DiegoGarzena/JJrepo/tree/main/points_along_3d_line_plugin'


def _advanced(param):
    param.setFlags(param.flags() | _FLAG_ADVANCED)
    return param


def _round(value, digits):
    return None if value is None else round(value, digits)


def _outside_valid(dist_2d, valid_range):
    """True if a point lies on the flat extension beyond the DTM edge."""
    return not (valid_range[0] - 1e-6 <= dist_2d <= valid_range[1] + 1e-6)


class PointsAlong3DLineAlgorithm(QgsProcessingAlgorithm):
    # Parameter ids (kept stable so that saved models keep working)
    INPUT_LINE = 'INPUT_LINE'
    INPUT_DTM = 'INPUT_DTM'
    DTM_BAND = 'DTM_BAND'
    INTERVAL = 'INTERVAL'
    START_OFFSET = 'START_OFFSET'
    INCLUDE_END = 'INCLUDE_END'
    EXTEND_OUTSIDE = 'EXTEND_OUTSIDE'
    DENSIFY_STEP = 'DENSIFY_STEP'
    LINE_ID_FIELD = 'LINE_ID_FIELD'
    CUSTOM_LINE_ID = 'CUSTOM_LINE_ID'
    TARGET_CRS = 'TARGET_CRS'

    ADD_LINE_ID = 'ADD_LINE_ID'
    ADD_PART_ID = 'ADD_PART_ID'
    ADD_SEQ_ID = 'ADD_SEQ_ID'
    ADD_COORDS = 'ADD_COORDS'
    ADD_Z_ELE = 'ADD_Z_ELE'
    ADD_DIST_2D = 'ADD_DIST_2D'
    ADD_DIST_3D = 'ADD_DIST_3D'
    ADD_DELTA_Z = 'ADD_DELTA_Z'
    ADD_SLOPE = 'ADD_SLOPE'
    ADD_AZIMUTH = 'ADD_AZIMUTH'

    OUTPUT = 'OUTPUT'

    # ------------------------------------------------------------------ meta
    def tr(self, text):
        return QCoreApplication.translate('PointsAlong3DLine', text)

    def createInstance(self):
        return PointsAlong3DLineAlgorithm()

    def name(self):
        return 'pointsalong3dline'

    def displayName(self):
        return self.tr('Points Along 3D Line')

    def group(self):
        return self.tr('3D Vector Tools')

    def groupId(self):
        return 'vector3d'

    def tags(self):
        return ['3d', 'points', 'line', 'dtm', 'elevation', 'slope',
                'azimuth', 'interval', 'sampling', 'profile']

    def shortDescription(self):
        return self.tr('Creates points at constant 3D distances along lines, '
                       'with elevation taken from a DTM.')

    def helpUrl(self):
        return HOME_URL

    def shortHelpString(self):
        return self.tr(
            '<p>Creates points spaced at a constant <b>3D distance</b> along '
            'line features, taking the elevation from a DTM raster. The '
            'interval is measured on the terrain surface, not on the '
            'horizontal plane.</p>'

            '<p><b>No pre-processing is needed.</b> Each line is densified '
            'internally, draped on the DTM with bilinear interpolation and '
            'then walked along its 3D length. Any Z values already stored in '
            'the input geometries are ignored.</p>'

            '<h3>Why densification matters</h3>'
            '<p>A DTM is a grid of cells, each with its own elevation, but a '
            'line may have only two vertices over hundreds of metres: from '
            'its geometry alone the tool would not know that the ground goes '
            'up and down in between. <b>Densification</b> adds intermediate '
            'vertices along the line (without moving it); the DTM elevation is '
            'read at each of them, and the 3D distance is measured along this '
            'polyline that follows the ground.</p>'
            '<p>The <b>densification step</b> is the distance between these '
            'vertices. A smaller step follows the relief more faithfully but '
            'takes longer. A step larger than the DTM pixel skips the terrain '
            'between vertices and <b>underestimates the 3D length</b> (points '
            'end up too far apart on the ground). A step much smaller than '
            'the pixel adds almost nothing, because the DTM has no more '
            'detail than that. The default (<b>0 = automatic</b>) is half of '
            'the DTM pixel size, which is a good choice in nearly all cases; '
            'change it only for special needs, such as a very fast preview '
            '(larger step) or an exceptionally detailed check (smaller '
            'step).</p>'

            '<h3>Parameters</h3>'
            '<ul>'
            '<li><b>Input line layer</b>: line or multiline features; '
            'the CRS must be projected (not geographic).</li>'
            '<li><b>DTM raster</b> and <b>band</b>: source of the elevation. '
            'It may have a different CRS from the lines. Its elevation unit '
            'must be the same as the horizontal unit of the line layer '
            '(normally metres).</li>'
            '<li><b>3D distance interval</b>: distance between consecutive '
            'points, measured along the draped line.</li>'
            '<li><b>Start offset</b>: 3D distance from the start of each '
            'line at which the first point is placed.</li>'
            '<li><b>Include end point</b>: also add the last vertex of the '
            'line when it does not fall on a regular interval.</li>'
            '<li><b>Keep points beyond the DTM edge</b>: only useful when '
            'a line extends past the edge of the DTM. By default (unchecked) '
            'points are created only where the DTM has a value, so the line '
            'is cut at the edge. If checked, points are also created beyond '
            'the edge, with a constant elevation equal to that of the nearest '
            'valid vertex (as if the ground were flat there), and they are '
            'flagged in the <i>z_extrap</i> field.</li>'
            '<li><b>Line ID field / custom ID</b>: value written in '
            '<i>line_id</i>. If no field is chosen, the custom text (plus '
            'a progressive number if there are several lines) or '
            '<i>Line_N</i> is used.</li>'
            '<li><b>Target CRS</b>: CRS of the <i>x_coord</i> and '
            '<i>y_coord</i> attributes. The output geometries keep the CRS '
            'of the input lines.</li>'
            '<li><b>Densification step</b> (advanced): see above. '
            '0 = automatic (half of the DTM pixel size).</li>'
            '</ul>'

            '<h3>Output attributes</h3>'
            '<ul>'
            '<li><b>line_id</b>, <b>part_id</b>, <b>seq_id</b>: line '
            'identifier, part number (multipart lines) and point number '
            'along the part, starting at 1.</li>'
            '<li><b>x_coord, y_coord</b>: coordinates in the target CRS.</li>'
            '<li><b>z_ele</b>: elevation interpolated from the DTM.</li>'
            '<li><b>z_extrap</b> (only if <i>Keep points beyond the DTM '
            'edge</i> is checked): 1 for points placed beyond the DTM edge '
            'with an assumed elevation, 0 for points with a real DTM '
            'elevation.</li>'
            '<li><b>dist_2d_p, dist_2d_tot</b>: horizontal distance from '
            'the previous point / from the start of the line.</li>'
            '<li><b>dist_3d_p, dist_3d_tot</b>: 3D distance from the '
            'previous point / from the start of the line (equal to the '
            'interval between regular points).</li>'
            '<li><b>delta_z_p</b>: elevation difference from the previous '
            'point.</li>'
            '<li><b>slope_deg, slope_pct</b>: slope of the step from the '
            'previous point; <b>positive uphill, negative downhill</b>. '
            'Empty for the first point.</li>'
            '<li><b>azimuth_deg</b>: grid bearing (0-360, clockwise from '
            'the +Y axis of the input CRS) of the step from the previous '
            'point; for the first point, towards the next one.</li>'
            '</ul>'

            '<h3>Notes</h3>'
            '<ul>'
            '<li>Distances are measured along the draped line, so they are '
            'path lengths rather than straight chords.</li>'
            '<li>If part of a line lies outside the DTM or on NoData, a '
            '<b>red warning</b> is shown in the log with the affected lines. '
            'Gaps in the middle of a line are bridged by linear interpolation '
            'of the elevation; they are never replaced with elevation 0.</li>'
            '<li>Lines that lie completely outside the DTM produce no '
            'points.</li>'
            '</ul>'
        )

    # ------------------------------------------------------------ parameters
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT_LINE, self.tr('Input line layer'), [_SOURCE_LINE]))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DTM, self.tr('DTM raster (elevation source)')))

        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.DTM_BAND, self.tr('DTM band'), defaultValue=1,
            parentLayerParameterName=self.INPUT_DTM)))

        interval = QgsProcessingParameterDistance(
            self.INTERVAL, self.tr('3D distance interval'),
            defaultValue=6.0, parentParameterName=self.INPUT_LINE)
        interval.setMetadata({'widget_wrapper': {'decimals': 3}})
        interval.setMinimum(0.001)
        self.addParameter(interval)

        offset = QgsProcessingParameterDistance(
            self.START_OFFSET, self.tr('Start offset (3D distance from line start)'),
            defaultValue=0.0, parentParameterName=self.INPUT_LINE)
        offset.setMinimum(0.0)
        self.addParameter(offset)

        self.addParameter(QgsProcessingParameterBoolean(
            self.INCLUDE_END, self.tr('Include end point of each line'),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterBoolean(
            self.EXTEND_OUTSIDE,
            self.tr('Keep points beyond the DTM edge (flat elevation of the '
                    'last valid point)'),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterField(
            self.LINE_ID_FIELD, self.tr('Line ID field (optional)'),
            optional=True, parentLayerParameterName=self.INPUT_LINE))

        self.addParameter(QgsProcessingParameterString(
            self.CUSTOM_LINE_ID,
            self.tr('Custom line ID / prefix (used when no field is selected)'),
            optional=True, defaultValue=''))

        self.addParameter(QgsProcessingParameterCrs(
            self.TARGET_CRS,
            self.tr('CRS for the x_coord / y_coord attributes'),
            defaultValue='ProjectCrs'))

        step = QgsProcessingParameterDistance(
            self.DENSIFY_STEP,
            self.tr('Densification step: distance between DTM sampling vertices '
                    '(0 = automatic, half DTM pixel)'),
            defaultValue=0.0, parentParameterName=self.INPUT_LINE)
        step.setMinimum(0.0)
        self.addParameter(_advanced(step))

        for pid, label in (
            (self.ADD_LINE_ID, 'Include line ID (line_id)'),
            (self.ADD_PART_ID, 'Include part number (part_id)'),
            (self.ADD_SEQ_ID, 'Include point sequence number (seq_id)'),
            (self.ADD_COORDS, 'Include coordinates (x_coord, y_coord)'),
            (self.ADD_Z_ELE, 'Include elevation (z_ele)'),
            (self.ADD_DIST_2D, 'Include 2D distances (dist_2d_p, dist_2d_tot)'),
            (self.ADD_DIST_3D, 'Include 3D distances (dist_3d_p, dist_3d_tot)'),
            (self.ADD_DELTA_Z, 'Include elevation difference (delta_z_p)'),
            (self.ADD_SLOPE, 'Include slope (slope_deg, slope_pct)'),
            (self.ADD_AZIMUTH, 'Include azimuth (azimuth_deg)'),
        ):
            self.addParameter(_advanced(QgsProcessingParameterBoolean(
                pid, self.tr(label), defaultValue=True)))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr('3D points')))

    def checkParameterValues(self, parameters, context):
        source = self.parameterAsSource(parameters, self.INPUT_LINE, context)
        if source is not None and source.sourceCrs().isValid() \
                and source.sourceCrs().isGeographic():
            return False, self.tr(
                'The input lines are in a geographic CRS (degrees). Reproject '
                'them to a projected CRS (metres) first, so that distances '
                'are meaningful.')
        return super().checkParameterValues(parameters, context)

    # --------------------------------------------------------------- helpers
    def _auto_step(self, dtm_layer, layer_crs, context, feedback):
        """Half of the DTM pixel size, expressed in the line layer units."""
        px = dtm_layer.rasterUnitsPerPixelX()
        py = dtm_layer.rasterUnitsPerPixelY()
        dtm_crs = dtm_layer.crs()
        size = min(px, py)
        if dtm_crs != layer_crs:
            try:
                tr = QgsCoordinateTransform(dtm_crs, layer_crs, context.transformContext())
                c = dtm_layer.extent().center()
                p0 = tr.transform(c)
                p1 = tr.transform(QgsPointXY(c.x() + px, c.y()))
                p2 = tr.transform(QgsPointXY(c.x(), c.y() + py))
                size = min(p0.distance(p1), p0.distance(p2))
            except QgsCsException:
                size = 0.0
        if not size or size <= 0 or math.isnan(size):
            feedback.pushWarning(self.tr(
                'Could not determine the DTM pixel size; using a 1 unit '
                'densification step.'))
            return 1.0
        return size / 2.0

    def _build_sampler(self, dtm_layer, band):
        # Processing runs in a background thread: read the raster through a
        # private copy of the data provider (the layer's own one belongs to
        # the main thread and is not thread-safe).
        provider = dtm_layer.dataProvider()
        try:
            provider = provider.clone() or provider
        except (AttributeError, TypeError):
            pass
        extent = provider.extent()
        width, height = provider.xSize(), provider.ySize()
        if width <= 0 or height <= 0:
            raise QgsProcessingException(self.tr('The DTM raster is empty.'))
        px = extent.width() / width
        py = extent.height() / height
        xmin, ymax = extent.xMinimum(), extent.yMaximum()

        def read_tile(c0, r0, ncols, nrows):
            rect = QgsRectangle(xmin + c0 * px, ymax - (r0 + nrows) * py,
                                xmin + (c0 + ncols) * px, ymax - r0 * py)
            block = provider.block(band, rect, ncols, nrows)
            if block is None or not block.isValid():
                return lambda r, c: None

            def getter(r, c):
                if block.isNoData(r, c):
                    return None
                v = block.value(r, c)
                return None if v != v else v  # NaN -> NoData

            return getter

        return GridSampler(xmin, ymax, px, py, width, height, read_tile)

    # ------------------------------------------------------------- algorithm
    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT_LINE, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT_LINE))
        dtm_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DTM, context)
        if dtm_layer is None:
            raise QgsProcessingException(self.tr('Invalid DTM raster layer.'))

        band = self.parameterAsInt(parameters, self.DTM_BAND, context)
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        start_offset = self.parameterAsDouble(parameters, self.START_OFFSET, context)
        include_end = self.parameterAsBool(parameters, self.INCLUDE_END, context)
        extend_outside = self.parameterAsBool(parameters, self.EXTEND_OUTSIDE, context)
        step = self.parameterAsDouble(parameters, self.DENSIFY_STEP, context)
        line_id_field = self.parameterAsString(parameters, self.LINE_ID_FIELD, context)
        custom_line_id = (self.parameterAsString(parameters, self.CUSTOM_LINE_ID, context) or '').strip()
        target_crs = self.parameterAsCrs(parameters, self.TARGET_CRS, context)

        opt = {k: self.parameterAsBool(parameters, k, context) for k in (
            self.ADD_LINE_ID, self.ADD_PART_ID, self.ADD_SEQ_ID, self.ADD_COORDS,
            self.ADD_Z_ELE, self.ADD_DIST_2D, self.ADD_DIST_3D, self.ADD_DELTA_Z,
            self.ADD_SLOPE, self.ADD_AZIMUTH)}

        if interval <= 0:
            raise QgsProcessingException(self.tr('The 3D distance interval must be greater than 0.'))

        layer_crs = source.sourceCrs()
        if layer_crs.isValid() and layer_crs.isGeographic():
            raise QgsProcessingException(self.tr(
                'The input lines are in a geographic CRS (degrees). Reproject '
                'them to a projected CRS (metres) first.'))
        if not target_crs.isValid():
            project = context.project()
            target_crs = project.crs() if project is not None else layer_crs

        # --- coordinate transforms --------------------------------------
        coord_transform = None
        if target_crs != layer_crs:
            coord_transform = QgsCoordinateTransform(layer_crs, target_crs, context.transformContext())
        coord_digits = 7 if target_crs.isGeographic() else 3

        to_dtm = None
        if dtm_layer.crs() != layer_crs:
            to_dtm = QgsCoordinateTransform(layer_crs, dtm_layer.crs(), context.transformContext())

        sampler = self._build_sampler(dtm_layer, band)

        def z_at(x, y):
            if to_dtm is None:
                return sampler.z_at(x, y)
            try:
                p = to_dtm.transform(QgsPointXY(x, y))
            except QgsCsException:
                return None
            return sampler.z_at(p.x(), p.y())

        if step <= 0:
            step = self._auto_step(dtm_layer, layer_crs, context, feedback)
            feedback.pushInfo(self.tr('Automatic densification step: {0:.4g}').format(step))

        # --- output fields ------------------------------------------------
        fields = QgsFields()

        def add(cond, name, kind):
            if cond:
                fields.append(QgsField(name, kind))

        add(opt[self.ADD_LINE_ID], 'line_id', _T_STRING)
        add(opt[self.ADD_PART_ID], 'part_id', _T_INT)
        add(opt[self.ADD_SEQ_ID], 'seq_id', _T_INT)
        add(opt[self.ADD_COORDS], 'x_coord', _T_DOUBLE)
        add(opt[self.ADD_COORDS], 'y_coord', _T_DOUBLE)
        add(opt[self.ADD_Z_ELE], 'z_ele', _T_DOUBLE)
        add(extend_outside, 'z_extrap', _T_INT)
        add(opt[self.ADD_DIST_2D], 'dist_2d_p', _T_DOUBLE)
        add(opt[self.ADD_DIST_2D], 'dist_2d_tot', _T_DOUBLE)
        add(opt[self.ADD_DIST_3D], 'dist_3d_p', _T_DOUBLE)
        add(opt[self.ADD_DIST_3D], 'dist_3d_tot', _T_DOUBLE)
        add(opt[self.ADD_DELTA_Z], 'delta_z_p', _T_DOUBLE)
        add(opt[self.ADD_SLOPE], 'slope_deg', _T_DOUBLE)
        add(opt[self.ADD_SLOPE], 'slope_pct', _T_DOUBLE)
        add(opt[self.ADD_AZIMUTH], 'azimuth_deg', _T_DOUBLE)

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, _POINT_Z, layer_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        def make_feature(rec, line_identifier, part_id, valid_range):
            attrs = []
            if opt[self.ADD_LINE_ID]:
                attrs.append(line_identifier)
            if opt[self.ADD_PART_ID]:
                attrs.append(part_id)
            if opt[self.ADD_SEQ_ID]:
                attrs.append(rec['seq_id'])
            if opt[self.ADD_COORDS]:
                cx, cy = rec['x'], rec['y']
                if coord_transform is not None:
                    try:
                        p = coord_transform.transform(QgsPointXY(cx, cy))
                        cx, cy = p.x(), p.y()
                    except QgsCsException:
                        cx = cy = None
                attrs.append(_round(cx, coord_digits))
                attrs.append(_round(cy, coord_digits))
            if opt[self.ADD_Z_ELE]:
                attrs.append(_round(rec['z'], 3))
            if extend_outside:
                attrs.append(1 if _outside_valid(rec['dist_2d_tot'], valid_range) else 0)
            if opt[self.ADD_DIST_2D]:
                attrs.append(_round(rec['dist_2d_p'], 3))
                attrs.append(_round(rec['dist_2d_tot'], 3))
            if opt[self.ADD_DIST_3D]:
                attrs.append(_round(rec['dist_3d_p'], 3))
                attrs.append(_round(rec['dist_3d_tot'], 3))
            if opt[self.ADD_DELTA_Z]:
                attrs.append(_round(rec['delta_z_p'], 3))
            if opt[self.ADD_SLOPE]:
                attrs.append(_round(rec['slope_deg'], 2))
                attrs.append(_round(rec['slope_pct'], 2))
            if opt[self.ADD_AZIMUTH]:
                az = _round(rec['azimuth_deg'], 2)
                attrs.append(None if az is None else az % 360.0)

            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry(QgsPoint(rec['x'], rec['y'], rec['z'])))
            f.setAttributes(attrs)
            return f

        # --- main loop --------------------------------------------------------
        field_names = [fld.name() for fld in source.fields()]
        use_field = bool(line_id_field) and line_id_field in field_names
        total = source.featureCount()
        progress_step = 100.0 / total if total else 0
        affected = []        # (line id, part no, vertices without DTM value, vertices)
        outside_parts = []   # (line id, part no) of parts entirely outside the DTM
        n_lines = n_points = n_skipped_parts = n_extrap_points = 0

        for idx, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(idx * progress_step))

            line_identifier = None
            if use_field:
                value = feat[line_id_field]
                if value is not None and value != NULL:
                    line_identifier = str(value)
            if line_identifier is None:
                if custom_line_id:
                    line_identifier = custom_line_id if total == 1 else '{0}_{1}'.format(custom_line_id, idx + 1)
                else:
                    line_identifier = 'Line_{0}'.format(idx + 1)

            geom = feat.geometry()
            if geom is None or geom.isNull() or geom.isEmpty():
                continue
            geom = QgsGeometry(geom)
            if QgsWkbTypes.isCurvedType(geom.wkbType()):
                geom.convertToStraightSegment()
            parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]

            n_lines += 1
            for part_no, part in enumerate(parts, start=1):
                if len(part) < 2:
                    n_skipped_parts += 1
                    continue
                xy = [(p.x(), p.y()) for p in part]
                path, valid_range, n_vert, n_miss = drape_vertices(
                    densify_xy(xy, step), z_at, extend_outside, feedback.isCanceled)
                if valid_range is None:
                    if n_vert:
                        outside_parts.append((line_identifier, part_no))
                    continue
                if n_miss:
                    affected.append((line_identifier, part_no, n_miss, n_vert))
                samples = list(resample_3d(path, interval, start_offset, include_end))
                if not samples:
                    n_skipped_parts += 1
                    continue
                for rec in build_records(samples):
                    if extend_outside and _outside_valid(rec['dist_2d_tot'], valid_range):
                        n_extrap_points += 1
                    sink.addFeature(make_feature(rec, line_identifier, part_no, valid_range),
                                    QgsFeatureSink.FastInsert)
                    n_points += 1

        # --- report -------------------------------------------------------------
        feedback.pushInfo(self.tr('Processed {0} line feature(s), created {1} point(s).').format(n_lines, n_points))
        if outside_parts:
            names = ', '.join('{0} (part {1})'.format(lid, no) for lid, no in outside_parts[:10])
            if len(outside_parts) > 10:
                names += ', ...'
            feedback.reportError(self.tr(
                '*** WARNING: {0} line part(s) lie completely outside the DTM '
                '(or on NoData): NO POINTS were created for them: {1} ***'
            ).format(len(outside_parts), names))

        if affected:
            rows = ['    - {0} (part {1}): {2} of {3} sampling vertices ({4:.0f}%)'.format(
                lid, no, miss, tot, 100.0 * miss / tot) for lid, no, miss, tot in affected[:10]]
            if len(affected) > 10:
                rows.append('    ... and {0} more'.format(len(affected) - 10))
            details = '\n'.join(rows)
            if extend_outside:
                feedback.pushWarning(self.tr(
                    '*** WARNING: part of the input lines lies outside the DTM '
                    'or on NoData ***\n'
                    'Affected lines (vertices without elevation):\n{0}\n'
                    '{1} point(s) beyond the DTM edge were kept with a constant '
                    'elevation (that of the nearest valid vertex) and are flagged '
                    'with z_extrap = 1. Gaps in the middle of a line are bridged by '
                    'linear interpolation of the elevation.'
                ).format(details, n_extrap_points))
            else:
                feedback.reportError(self.tr(
                    '*** WARNING: part of the input lines lies outside the DTM '
                    'or on NoData ***\n'
                    'Affected lines (vertices without elevation):\n{0}\n'
                    'No points were created where the DTM has no value at the ends '
                    'of a line (distances are measured from the first vertex with a '
                    'valid elevation). Gaps in the middle of a line are bridged by '
                    'linear interpolation of the elevation.\n'
                    'To keep the points beyond the DTM edge, enable "Keep points '
                    'beyond the DTM edge".'
                ).format(details))

        if n_skipped_parts:
            feedback.pushWarning(self.tr(
                '{0} line part(s) produced no points (too short, or start offset '
                'longer than the line).').format(n_skipped_parts))

        return {self.OUTPUT: dest_id}


class PointsAlong3DLineProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(PointsAlong3DLineAlgorithm())

    def id(self):
        return 'pointsalong3dline_provider'

    def name(self):
        return 'Points Along 3D Line Tools'

    def longName(self):
        return self.name()
