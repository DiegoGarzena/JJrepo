from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
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
            "Genera punti a distanza tridimensionale (3D) reale e costante lungo una linea.<br><br>"
            "<b>MANDATORY PRE-PROCESSING STEPS / PASSAGGI PRELIMINARI OBBLIGATORI:</b><br>"
            "Per garantire la massima precisione ed efficienza, prima di eseguire questo strumento è "
            "obbligatorio preparare la linea eseguendo questi 2 passaggi nell'ordine esatto:<br><br>"
            "1. <b>Densify (Densifica)</b><br>"
            "   - <i>English:</i> <b>Densify by interval</b><br>"
            "   - <i>Italiano:</i> <b>Densifica tramite intervallo</b><br>"
            "   - Imposta un intervallo piccolo (es. 0.1 o 0.2 m) per infittire i vertici della linea e consentirle di seguire l'andamento del terreno.<br><br>"
            "2. <b>Drape (Adagia sul DTM)</b><br>"
            "   - <i>English:</i> <b>Drape (set Z value from raster)</b><br>"
            "   - <i>Italiano:</i> <b>Drape (imposta valore Z da raster)</b><br>"
            "   - Proietta la linea densificata sul DTM per assegnare la quota Z reale a ciascun vertice.<br><br>"
            "<i>Nota: Se non si eseguono questi passaggi in ordine, il calcolo della distanza 3D avverrà "
            "solo tra i vertici originari o richiederà un campionamento continuo del raster rallentando l'elaborazione.</i>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LINE,
                'Layer Linea di Input / Input Line Layer',
                [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DTM,
                'Raster DTM (per la quota Z / for Z values)'
            )
        )
        self.addParameter(
            QgsProcessingParameterDistance(
                self.INTERVAL,
                'Distanza 3D tra i punti (metri) / 3D Distance interval (meters)',
                defaultValue=6.0,
                parentParameterName=self.INPUT_LINE
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                'Punti 3D Generati / Output 3D Points'
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        line_layer = self.parameterAsVectorLayer(parameters, self.INPUT_LINE, context)
        dtm_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DTM, context)
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)

        # Definisce i campi del layer di output
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
            # Se la geometria del vertice ha già Z valida la usa, altrimenti la campiona dal DTM
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

                # Primo punto della linea (a distanza 0.0 m)
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

                    # Distanza Euclidea 3D nello spazio
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