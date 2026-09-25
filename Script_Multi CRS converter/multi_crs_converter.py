from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFeatureSink,
    QgsProject,
    QgsField,
    QgsFields,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink
)
from qgis.PyQt.QtCore import QVariant

class MultiCrsConverter(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    OUTPUT_MODE = 'OUTPUT_MODE'
    OUTPUT = 'OUTPUT'

    def createInstance(self):
        return MultiCrsConverter()

    def name(self):
        return 'multi_crs_converter'

    def displayName(self):
        return 'Add Multi-CRS Coordinates'

    def group(self):
        return 'Custom Scripts'

    def groupId(self):
        return 'custom'

    def initAlgorithm(self, config=None):
        # Input point layer selection
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LAYER,
                'Select Point Layer',
                types=[QgsProcessing.TypeVectorPoint]
            )
        )

        # Mode choice: Modify existing layer or create a new one
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.OUTPUT_MODE,
                'Modify original layer in-place (Uncheck to create a new layer)',
                defaultValue=False
            )
        )

        # Output parameter when creating a new layer
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                'Multi CRS Layer',
                type=QgsProcessing.TypeVectorPoint
            )
        )

        # Optional CRS parameters (up to 5)
        for i in range(1, 6):
            default_crs = 'EPSG:4326' if i == 1 else ('EPSG:32632' if i == 2 else 'EPSG:3003')
            self.addParameter(
                QgsProcessingParameterBoolean(
                    f'USE_CRS_{i}',
                    f'Enable System {i}',
                    defaultValue=(i == 1)
                )
            )
            self.addParameter(
                QgsProcessingParameterCrs(
                    f'CRS_{i}',
                    f'  └─ Select CRS {i}',
                    defaultValue=default_crs
                )
            )

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsVectorLayer(parameters, self.INPUT_LAYER, context)
        if not layer:
            return {}

        in_place = self.parameterAsBoolean(parameters, self.OUTPUT_MODE, context)
        source_crs = layer.crs()
        selected_crs_list = []

        # Retrieve selected CRS choices
        for i in range(1, 6):
            if self.parameterAsBoolean(parameters, f'USE_CRS_{i}', context):
                target_crs = self.parameterAsCrs(parameters, f'CRS_{i}', context)
                selected_crs_list.append(target_crs)

        if not selected_crs_list:
            feedback.pushInfo("No Coordinate Reference Systems selected. Process canceled.")
            return {}

        # Prepare field names and coordinate transformations
        transforms = []
        new_field_objects = []

        for crs in selected_crs_list:
            clean_epsg = crs.authid().replace(':', '_').replace('-', '_')
            field_x = f"X_{clean_epsg}"
            field_y = f"Y_{clean_epsg}"

            new_field_objects.append(QgsField(field_x, QVariant.Double, len=20, prec=7))
            new_field_objects.append(QgsField(field_y, QVariant.Double, len=20, prec=7))

            transform = QgsCoordinateTransform(source_crs, crs, QgsProject.instance())
            transforms.append((field_x, field_y, transform))

        total_features = layer.featureCount()

        # MODE 1: Update original layer in-place
        if in_place:
            layer.startEditing()
            fields_to_add = []
            
            for field_obj in new_field_objects:
                if layer.fields().indexFromName(field_obj.name()) == -1:
                    fields_to_add.append(field_obj)

            if fields_to_add:
                layer.dataProvider().addAttributes(fields_to_add)
                layer.updateFields()

            field_indices = {name: layer.fields().indexFromName(name) for name in layer.fields().names()}

            for count, feature in enumerate(layer.getFeatures()):
                if feedback.isCanceled():
                    break

                geom = feature.geometry()
                if geom and not geom.isEmpty():
                    point = geom.asMultiPoint()[0] if geom.isMultipart() else geom.asPoint()
                    if point:
                        updates = {}
                        for fx, fy, xform in transforms:
                            try:
                                tr_point = xform.transform(point)
                                updates[field_indices[fx]] = tr_point.x()
                                updates[field_indices[fy]] = tr_point.y()
                            except Exception as e:
                                feedback.pushDebugInfo(f"Error on feature ID {feature.id()}: {str(e)}")

                        if updates:
                            layer.changeAttributeValues(feature.id(), updates)

                if total_features > 0:
                    feedback.setProgress(int((count + 1) / total_features * 100))

            layer.commitChanges()
            feedback.pushInfo("Layer attributes updated in-place successfully!")
            return {}

        # MODE 2: Create a new output layer
        else:
            output_fields = QgsFields(layer.fields())
            for field_obj in new_field_objects:
                if output_fields.indexFromName(field_obj.name()) == -1:
                    output_fields.append(field_obj)

            (sink, dest_id) = self.parameterAsSink(
                parameters,
                self.OUTPUT,
                context,
                output_fields,
                layer.wkbType(),
                source_crs
            )

            field_indices = {name: output_fields.indexFromName(name) for name in output_fields.names()}

            for count, feature in enumerate(layer.getFeatures()):
                if feedback.isCanceled():
                    break

                new_feat = QgsFeature(output_fields)
                new_feat.setGeometry(feature.geometry())
                
                # Copy existing attribute values
                attr_map = feature.attributes()
                for idx, val in enumerate(attr_map):
                    new_feat.setAttribute(idx, val)

                geom = feature.geometry()
                if geom and not geom.isEmpty():
                    point = geom.asMultiPoint()[0] if geom.isMultipart() else geom.asPoint()
                    if point:
                        for fx, fy, xform in transforms:
                            try:
                                tr_point = xform.transform(point)
                                new_feat.setAttribute(field_indices[fx], tr_point.x())
                                new_feat.setAttribute(field_indices[fy], tr_point.y())
                            except Exception as e:
                                feedback.pushDebugInfo(f"Error on feature ID {feature.id()}: {str(e)}")

                sink.addFeature(new_feat, QgsFeatureSink.FastInsert)

                if total_features > 0:
                    feedback.setProgress(int((count + 1) / total_features * 100))

            feedback.pushInfo("New layer created with multi-CRS coordinates successfully!")
            return {self.OUTPUT: dest_id}