from qgis.core import (
    QgsProcessingProvider,
    QgsProcessingAlgorithm,
    QgsProcessing,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterDistance,
    QgsProcessingParameterFeatureSink,
    QgsFeature,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsWkbTypes,
    QgsFields,
    QgsField
)
from qgis.PyQt.QtCore import QVariant
import math


class PointsAlong3DLineAlgorithm(QgsProcessingAlgorithm):
    INPUT_LINE = 'INPUT_LINE'
    INPUT_DTM = 'INPUT_DTM'
    INTERVAL = 'INTERVAL'
    OUTPUT = 'OUTPUT'

    def createInstance(self):
        return PointsAlong3DLineAlgorithm()

    def name(self):
        return 'pointsalong3dline'

    def displayName(self):
        return 'Points Along 3D Line'

    def group(self):
        return '3D Vector Tools'

    def groupId(self):
        return 'vector3d'

    def shortHelpString(self):
        return (
            "Generates points at a constant 3D spatial distance along a line using a DTM raster for Z coordinates.<br><br>"
            "<b>MANDATORY PRE-PROCESSING STEPS:</b><br>"
            "To ensure accurate 3D distance calculations and high execution performance, "
            "you MUST prepare your line geometry in the following exact order:<br><br>"
            "1. <b>Densify by interval</b><br>"
            "   - <i>Tool:</i> <b>Densify by interval</b><br>"
            "   - Set a small interval (e.g., 0.1 or 0.2 meters) to add intermediate vertices along the line so it can closely follow the terrain profile.<br><br>"
            "2. <b>Drape (set Z value from raster)</b><br>"
            "   - <i>Tool:</i> <b>Drape (set Z value from raster)</b><br>"
            "   - Overlay the densified line onto your DTM raster to assign actual elevation (Z coordinates) to every vertex.<br><br>"
            "<i>Note: Skipping these pre-processing steps will result in 3D distances being calculated "
            "only between original vertices or will require continuous raster sampling, significantly slowing down processing.</i>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LINE,
                'Input Line Layer',
                [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DTM,
                'DTM Raster Layer (for Z elevation)'
            )
        )
        self.addParameter(
            QgsProcessingParameterDistance(
                self.INTERVAL,
                '3D Distance Interval (meters)',
                defaultValue=6.0,
                parentParameterName=self.INPUT_LINE
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                'Output 3D Points'
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        line_layer = self.parameterAsVectorLayer(parameters, self.INPUT_LINE, context)
        dtm_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DTM, context)
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)

        fields = QgsFields()
        fields.append(QgsField("dist_3d", QVariant.Double))

        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.PointZ,
            line_layer.crs()
        )

        dtm_provider = dtm_layer.dataProvider()

        def get_z(pt):
            if hasattr(pt, 'z') and not math.isnan(pt.z()):
                return pt.z()
            val, ok = dtm_provider.sample(QgsPointXY(pt.x(), pt.y()), 1)
            return val if ok and not math.isnan(val) else 0.0

        for feat in line_layer.getFeatures():
            if feedback.isCanceled():
                break

            geom = feat.geometry()
            lines = []
            if geom.isMultipart():
                multi = geom.asMultiPolyline()
                if multi:
                    lines = multi
            else:
                single = geom.asPolyline()
                if single:
                    lines = [single]

            if not lines:
                lines = [[p for p in geom.vertices()]]

            for line in lines:
                if len(line) < 2:
                    continue

                z0 = get_z(line[0])
                f_first = QgsFeature(fields)
                f_first.setGeometry(QgsGeometry(QgsPoint(line[0].x(), line[0].y(), z0)))
                f_first.setAttribute("dist_3d", 0.0)
                sink.addFeature(f_first)

                acc_dist = 0.0
                target_dist = interval

                for i in range(len(line) - 1):
                    p1, p2 = line[i], line[i+1]
                    z1, z2 = get_z(p1), get_z(p2)

                    dx = p2.x() - p1.x()
                    dy = p2.y() - p1.y()
                    dz = z2 - z1

                    seg_dist = math.sqrt(dx*dx + dy*dy + dz*dz)

                    while acc_dist + seg_dist >= target_dist:
                        remain = target_dist - acc_dist
                        ratio = remain / seg_dist if seg_dist > 0 else 0

                        nx = p1.x() + ratio * dx
                        ny = p1.y() + ratio * dy
                        nz = z1 + ratio * dz

                        f = QgsFeature(fields)
                        f.setGeometry(QgsGeometry(QgsPoint(nx, ny, nz)))
                        f.setAttribute("dist_3d", target_dist)
                        sink.addFeature(f)

                        target_dist += interval

                    acc_dist += seg_dist

        return {self.OUTPUT: dest_id}


class PointsAlong3DLineProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(PointsAlong3DLineAlgorithm())

    def id(self):
        return 'pointsalong3dline_provider'

    def name(self):
        return 'Points Along 3D Line Tools'

    def icon(self):
        return QgsProcessingProvider.icon(self)
