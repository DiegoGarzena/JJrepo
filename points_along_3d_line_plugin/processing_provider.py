from qgis.core import (
    QgsProcessingProvider,
    QgsProcessingAlgorithm,
    QgsProcessing,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterDistance,
    QgsProcessingParameterCrs,
    QgsProcessingParameterField,
    QgsProcessingParameterString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsFeature,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsWkbTypes,
    QgsFields,
    QgsField,
    QgsCoordinateTransform,
    QgsProject
)
from qgis.PyQt.QtCore import QVariant
import math


class PointsAlong3DLineAlgorithm(QgsProcessingAlgorithm):
    INPUT_LINE = 'INPUT_LINE'
    INPUT_DTM = 'INPUT_DTM'
    INTERVAL = 'INTERVAL'
    TARGET_CRS = 'TARGET_CRS'
    
    # Gestione ID Linea
    LINE_ID_FIELD = 'LINE_ID_FIELD'
    CUSTOM_LINE_ID = 'CUSTOM_LINE_ID'
    
    # Parametri opzionali per gli attributi
    ADD_LINE_ID = 'ADD_LINE_ID'
    ADD_SEQ_ID = 'ADD_SEQ_ID'
    ADD_COORDS = 'ADD_COORDS'
    ADD_Z_ELE = 'ADD_Z_ELE'
    ADD_DIST_2D = 'ADD_DIST_2D'
    ADD_DIST_3D = 'ADD_DIST_3D'
    ADD_DELTA_Z = 'ADD_DELTA_Z'
    ADD_SLOPE = 'ADD_SLOPE'
    ADD_AZIMUTH = 'ADD_AZIMUTH'
    
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
            "<b>Points Along 3D Line</b> generates 3D point features spaced at constant 3D spatial distances "
            "along line geometries using a DTM raster to assign elevation.<br><br>"
            "<b>MANDATORY PRE-PROCESSING STEPS:</b><br>"
            "To guarantee precise 3D distance calculations and optimal performance, prepare your line layer in this exact order:<br>"
            "1. <b>Densify by interval:</b> Add intermediate vertices along the line at a small interval (e.g., 0.1m - 0.5m) so the line follows terrain topography.<br>"
            "2. <b>Drape (set Z value from raster):</b> Assign true Z coordinates from your DTM raster to every vertex.<br><br>"
            "<b>GENERATED ATTRIBUTES:</b><br>"
            "• <b>line_id:</b> Line identifier selected from attribute field, custom text, or default sequence.<br>"
            "• <b>seq_id:</b> Sequential point index along each line (starting at 1).<br>"
            "• <b>x_coord, y_coord:</b> Planimetric coordinates in the selected Target CRS.<br>"
            "• <b>z_ele:</b> Terrain elevation (Z) extracted from DTM / 3D geometry.<br>"
            "• <b>dist_2d_p, dist_2d_tot:</b> Step and cumulative 2D distances (meters).<br>"
            "• <b>dist_3d_p, dist_3d_tot:</b> Step (constant interval) and cumulative 3D distances (meters).<br>"
            "• <b>delta_z_p:</b> Elevation difference relative to the previous point.<br>"
            "• <b>slope_deg, slope_pct:</b> Segment slope in degrees and percentage.<br>"
            "• <b>azimuth_deg:</b> Bearing angle (0° - 360°) from the previous point.<br>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LINE,
                'Input Line Layer',
                [QgsProcessing.SourceType.TypeVectorLine]
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
        
        # Selezione campo ID linea o prefisso/nome manuale
        self.addParameter(
            QgsProcessingParameterField(
                self.LINE_ID_FIELD,
                'Line ID Attribute Field (Optional)',
                optional=True,
                parentLayerParameterName=self.INPUT_LINE
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.CUSTOM_LINE_ID,
                'Custom Line Name / ID Prefix (Used if Field above is empty)',
                optional=True,
                defaultValue=''
            )
        )
        
        # CRS di destinazione per le coordinate in tabella (Default: CRS del progetto)
        self.addParameter(
            QgsProcessingParameterCrs(
                self.TARGET_CRS,
                'Target CRS for Attribute Coordinates (x_coord, y_coord)',
                defaultValue='ProjectCrs'
            )
        )

        # Spunte/Checkbox per la tabella degli attributi
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_LINE_ID,
                'Include Line Name/ID (line_id)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_SEQ_ID,
                'Include Sequential ID (seq_id)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_COORDS,
                'Include Coordinates (x_coord, y_coord)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_Z_ELE,
                'Include Z Elevation (z_ele)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_DIST_2D,
                'Include 2D Distances (dist_2d_p, dist_2d_tot)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_DIST_3D,
                'Include 3D Distances (dist_3d_p, dist_3d_tot)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_DELTA_Z,
                'Include Step Elevation Difference (delta_z_p)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_SLOPE,
                'Include Slope fields (slope_deg, slope_pct)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_AZIMUTH,
                'Include Direction Azimuth (azimuth_deg)',
                defaultValue=True
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                '3D Sampled Points'
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        line_layer = self.parameterAsVectorLayer(parameters, self.INPUT_LINE, context)
        dtm_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DTM, context)
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        target_crs = self.parameterAsCrs(parameters, self.TARGET_CRS, context)

        line_id_field = self.parameterAsString(parameters, self.LINE_ID_FIELD, context)
        custom_line_id = self.parameterAsString(parameters, self.CUSTOM_LINE_ID, context)

        # Lettura delle spunte attive
        add_line_id = self.parameterAsBool(parameters, self.ADD_LINE_ID, context)
        add_seq_id = self.parameterAsBool(parameters, self.ADD_SEQ_ID, context)
        add_coords = self.parameterAsBool(parameters, self.ADD_COORDS, context)
        add_z_ele = self.parameterAsBool(parameters, self.ADD_Z_ELE, context)
        add_dist_2d = self.parameterAsBool(parameters, self.ADD_DIST_2D, context)
        add_dist_3d = self.parameterAsBool(parameters, self.ADD_DIST_3D, context)
        add_delta_z = self.parameterAsBool(parameters, self.ADD_DELTA_Z, context)
        add_slope = self.parameterAsBool(parameters, self.ADD_SLOPE, context)
        add_azimuth = self.parameterAsBool(parameters, self.ADD_AZIMUTH, context)

        # Configurazione Trasformazione di Coordinate per x_coord e y_coord
        layer_crs = line_layer.crs()
        if not target_crs.isValid():
            target_crs = QgsProject.instance().crs()
        transform = QgsCoordinateTransform(layer_crs, target_crs, context.transformContext())

        # Costruzione dei campi della tabella
        fields = QgsFields()
        if add_line_id:
            fields.append(QgsField("line_id", QVariant.String))
        if add_seq_id:
            fields.append(QgsField("seq_id", QVariant.Int))
        if add_coords:
            fields.append(QgsField("x_coord", QVariant.Double))
            fields.append(QgsField("y_coord", QVariant.Double))
        if add_z_ele:
            fields.append(QgsField("z_ele", QVariant.Double))
        if add_dist_2d:
            fields.append(QgsField("dist_2d_p", QVariant.Double))
            fields.append(QgsField("dist_2d_tot", QVariant.Double))
        if add_dist_3d:
            fields.append(QgsField("dist_3d_p", QVariant.Double))
            fields.append(QgsField("dist_3d_tot", QVariant.Double))
        if add_delta_z:
            fields.append(QgsField("delta_z_p", QVariant.Double))
        if add_slope:
            fields.append(QgsField("slope_deg", QVariant.Double))
            fields.append(QgsField("slope_pct", QVariant.Double))
        if add_azimuth:
            fields.append(QgsField("azimuth_deg", QVariant.Double))

        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.Type.PointZ,
            layer_crs
        )

        dtm_provider = dtm_layer.dataProvider()

        def get_z(pt):
            if hasattr(pt, 'z') and not math.isnan(pt.z()):
                return pt.z()
            val, ok = dtm_provider.sample(QgsPointXY(pt.x(), pt.y()), 1)
            return val if ok and not math.isnan(val) else 0.0

        line_index = 0
        for feat in line_layer.getFeatures():
            if feedback.isCanceled():
                break

            line_index += 1

            # Logica determinazione Line ID:
            # 1. Se è stato scelto un campo valido dal menu a tendina
            if line_id_field and line_id_field in [f.name() for f in feat.fields()]:
                line_identifier = str(feat[line_id_field])
            # 2. Se è stato digitato un nome manuale/custom
            elif custom_line_id.strip():
                line_identifier = custom_line_id.strip() if line_layer.featureCount() == 1 else f"{custom_line_id.strip()}_{line_index}"
            # 3. Default fallback
            else:
                line_identifier = f"Line_{line_index}"

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

                seq_id = 1

                # Primo punto (Inizio linea)
                p_first = line[0]
                z_first = get_z(p_first)
                pt_trans_first = transform.transform(QgsPointXY(p_first.x(), p_first.y()))

                dx_init = line[1].x() - p_first.x()
                dy_init = line[1].y() - p_first.y()
                azimuth_first = (math.degrees(math.atan2(dx_init, dy_init)) + 360.0) % 360.0

                f_first = QgsFeature(fields)
                f_first.setGeometry(QgsGeometry(QgsPoint(p_first.x(), p_first.y(), z_first)))

                if add_line_id: f_first.setAttribute("line_id", line_identifier)
                if add_seq_id: f_first.setAttribute("seq_id", seq_id)
                if add_coords:
                    f_first.setAttribute("x_coord", round(pt_trans_first.x(), 4 if target_crs.isGeographic() else 3))
                    f_first.setAttribute("y_coord", round(pt_trans_first.y(), 4 if target_crs.isGeographic() else 3))
                if add_z_ele: f_first.setAttribute("z_ele", round(z_first, 3))
                if add_dist_2d:
                    f_first.setAttribute("dist_2d_p", 0.0)
                    f_first.setAttribute("dist_2d_tot", 0.0)
                if add_dist_3d:
                    f_first.setAttribute("dist_3d_p", 0.0)
                    f_first.setAttribute("dist_3d_tot", 0.0)
                if add_delta_z: f_first.setAttribute("delta_z_p", 0.0)
                if add_slope:
                    f_first.setAttribute("slope_deg", 0.0)
                    f_first.setAttribute("slope_pct", 0.0)
                if add_azimuth: f_first.setAttribute("azimuth_deg", round(azimuth_first, 2))

                sink.addFeature(f_first)

                prev_x, prev_y, prev_z = p_first.x(), p_first.y(), z_first
                cum_dist_2d = 0.0

                current_target_3d = interval
                accumulated_3d = 0.0

                for i in range(len(line) - 1):
                    p1, p2 = line[i], line[i+1]
                    z1, z2 = get_z(p1), get_z(p2)

                    dx = p2.x() - p1.x()
                    dy = p2.y() - p1.y()
                    dz = z2 - z1

                    seg_dist_3d = math.sqrt(dx*dx + dy*dy + dz*dz)
                    if seg_dist_3d == 0:
                        continue

                    while accumulated_3d + seg_dist_3d >= current_target_3d:
                        needed_3d = current_target_3d - accumulated_3d
                        ratio = needed_3d / seg_dist_3d

                        nx = p1.x() + ratio * dx
                        ny = p1.y() + ratio * dy
                        nz = z1 + ratio * dz

                        seq_id += 1

                        d2d_prev = math.sqrt((nx - prev_x)**2 + (ny - prev_y)**2)
                        dz_prev = nz - prev_z
                        cum_dist_2d += d2d_prev

                        d3d_prev = interval
                        d3d_tot = (seq_id - 1) * interval

                        # Pendenza
                        if d2d_prev > 0:
                            slope_rad = math.atan(abs(dz_prev) / d2d_prev)
                            slope_deg = math.degrees(slope_rad)
                            slope_pct = (abs(dz_prev) / d2d_prev) * 100.0
                        else:
                            slope_deg = 0.0
                            slope_pct = 0.0

                        # Azimuth
                        step_dx = nx - prev_x
                        step_dy = ny - prev_y
                        azimuth_deg = (math.degrees(math.atan2(step_dx, step_dy)) + 360.0) % 360.0

                        pt_trans = transform.transform(QgsPointXY(nx, ny))

                        f = QgsFeature(fields)
                        f.setGeometry(QgsGeometry(QgsPoint(nx, ny, nz)))

                        if add_line_id: f.setAttribute("line_id", line_identifier)
                        if add_seq_id: f.setAttribute("seq_id", seq_id)
                        if add_coords:
                            f.setAttribute("x_coord", round(pt_trans.x(), 4 if target_crs.isGeographic() else 3))
                            f.setAttribute("y_coord", round(pt_trans.y(), 4 if target_crs.isGeographic() else 3))
                        if add_z_ele: f.setAttribute("z_ele", round(nz, 3))
                        if add_dist_2d:
                            f.setAttribute("dist_2d_p", round(d2d_prev, 3))
                            f.setAttribute("dist_2d_tot", round(cum_dist_2d, 3))
                        if add_dist_3d:
                            f.setAttribute("dist_3d_p", round(d3d_prev, 3))
                            f.setAttribute("dist_3d_tot", round(d3d_tot, 3))
                        if add_delta_z: f.setAttribute("delta_z_p", round(dz_prev, 3))
                        if add_slope:
                            f.setAttribute("slope_deg", round(slope_deg, 2))
                            f.setAttribute("slope_pct", round(slope_pct, 2))
                        if add_azimuth: f.setAttribute("azimuth_deg", round(azimuth_deg, 2))

                        sink.addFeature(f)

                        prev_x, prev_y, prev_z = nx, ny, nz
                        current_target_3d += interval

                    accumulated_3d += seg_dist_3d

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