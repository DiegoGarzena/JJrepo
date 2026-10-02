# -*- coding: utf-8 -*-
from qgis.core import QgsApplication

from .processing_provider import PointsAlong3DLineProvider


class PointsAlong3DLinePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initProcessing(self):
        self.provider = PointsAlong3DLineProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
