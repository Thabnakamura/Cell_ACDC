import os
import sys
import re
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle, Circle, PathPatch, Path
import numpy as np
import scipy.interpolate
import tkinter as tk
import cv2
import traceback
from itertools import combinations
from natsort import natsorted
# from MyWidgets import Slider, Button, MyRadioButtons
from skimage.measure import label, regionprops
import skimage.filters
import skimage.measure
import skimage.morphology
import skimage.exposure
import skimage.draw
import skimage.registration
import skimage.color
import skimage.segmentation
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)
from pyglet.canvas import Display
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import math
import time

import pyqtgraph as pg
from pyqtgraph.Qt import QtGui
from PyQt5 import QtCore
from PyQt5.QtGui import QIcon, QFontMetrics
from PyQt5.QtCore import Qt, QSize, QEvent
from PyQt5.QtWidgets import (
    QAction, QApplication, QMainWindow, QMenu, QLabel, QToolBar,
    QScrollBar, QWidget, QVBoxLayout, QLineEdit, QPushButton,
    QHBoxLayout, QDialog, QFormLayout, QListWidget, QAbstractItemView,
    QButtonGroup, QCheckBox, QSizePolicy, QComboBox, QSlider, QGridLayout,
    QSpinBox, QToolButton, QTableView, QTextBrowser, QDoubleSpinBox,
    QScrollArea, QFrame
)

import myutils, load, prompts

import qrc_resources


pg.setConfigOption('imageAxisOrder', 'row-major') # best performance

class QDialogMetadataXML(QDialog):
    def __init__(
            self, title='Metadata',
            LensNA=1.0, DimensionOrder='',
            SizeT=1, SizeZ=1, SizeC=1, SizeS=1,
            TimeIncrement=180.0, TimeIncrementUnit='s',
            PhysicalSizeX=1.0, PhysicalSizeY=1.0, PhysicalSizeZ=1.0,
            PhysicalSizeUnit='μm', chNames=None, emWavelens=None,
            parent=None, rawDataStruct=None
        ):

        self.cancel = True
        self.trust = False
        self.overWrite = False
        super().__init__(parent)
        self.setWindowTitle(title)
        font = QtGui.QFont()
        font.setPointSize(10)
        self.setFont(font)

        mainLayout = QVBoxLayout()
        entriesLayout = QGridLayout()
        self.channelNameLayouts = (QVBoxLayout(), QVBoxLayout(), QVBoxLayout())
        self.channelEmWLayouts = (QVBoxLayout(), QVBoxLayout(), QVBoxLayout())
        buttonsLayout = QGridLayout()

        infoLabel = QLabel()
        infoTxt = (
            '<b>Confirm/Edit</b> the <b>metadata</b> below.'
        )
        infoLabel.setText(infoTxt)
        # padding: top, left, bottom, right
        infoLabel.setStyleSheet("font-size:12pt; padding:0px 0px 5px 0px;")
        mainLayout.addWidget(infoLabel, alignment=Qt.AlignCenter)

        noteLabel = QLabel()
        noteLabel.setText(
            f'NOTE: If you are not sure about some of the entries '
            'you can try to click "Ok".\n'
            'If they are wrong you will get '
            'an error message later when trying to read the data.'
        )
        noteLabel.setAlignment(Qt.AlignCenter)
        mainLayout.addWidget(noteLabel, alignment=Qt.AlignCenter)

        row = 0
        self.SizeS_SB = QSpinBox()
        self.SizeS_SB.setAlignment(Qt.AlignCenter)
        self.SizeS_SB.setMinimum(1)
        self.SizeS_SB.setMaximum(2147483647)
        self.SizeS_SB.setValue(SizeS)
        txt = 'Number of positions (S):  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.SizeS_SB, row, 1)

        row += 1
        self.LensNA_DSB = QDoubleSpinBox()
        self.LensNA_DSB.setAlignment(Qt.AlignCenter)
        self.LensNA_DSB.setSingleStep(0.1)
        self.LensNA_DSB.setValue(LensNA)
        txt = 'Numerical Aperture Objective Lens:  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.LensNA_DSB, row, 1)

        row += 1
        self.DimensionOrder_QLE = QLineEdit()
        self.DimensionOrder_QLE.setAlignment(Qt.AlignCenter)
        self.DimensionOrder_QLE.setText(DimensionOrder)
        txt = 'Order of dimensions:  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.DimensionOrder_QLE, row, 1)

        row += 1
        self.SizeT_SB = QSpinBox()
        self.SizeT_SB.setAlignment(Qt.AlignCenter)
        self.SizeT_SB.setMinimum(1)
        self.SizeT_SB.setMaximum(2147483647)
        self.SizeT_SB.setValue(SizeT)
        txt = 'Number of frames (T):  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.SizeT_SB, row, 1)

        row += 1
        self.SizeZ_SB = QSpinBox()
        self.SizeZ_SB.setAlignment(Qt.AlignCenter)
        self.SizeZ_SB.setMinimum(1)
        self.SizeZ_SB.setMaximum(2147483647)
        self.SizeZ_SB.setValue(SizeZ)
        txt = 'Number of z-slices in the z-stack (Z):  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.SizeZ_SB, row, 1)
        self.SizeZ_SB.valueChanged.connect(self.hideShowPhysicalSizeZ)

        row += 1
        self.TimeIncrement_DSB = QDoubleSpinBox()
        self.TimeIncrement_DSB.setAlignment(Qt.AlignCenter)
        self.TimeIncrement_DSB.setMaximum(2147483647.0)
        self.TimeIncrement_DSB.setSingleStep(1)
        self.TimeIncrement_DSB.setDecimals(3)
        self.TimeIncrement_DSB.setValue(TimeIncrement)
        txt = 'Frame interval:  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.TimeIncrement_DSB, row, 1)

        self.TimeIncrementUnit_CB = QComboBox()
        unitItems = [
            'ms', 'seconds', 'minutes', 'hours'
        ]
        currentTxt = [unit for unit in unitItems
                      if unit.startswith(TimeIncrementUnit)]
        self.TimeIncrementUnit_CB.addItems(unitItems)
        if currentTxt:
            self.TimeIncrementUnit_CB.setCurrentText(currentTxt[0])
        entriesLayout.addWidget(self.TimeIncrementUnit_CB, row, 2)

        row += 1
        self.PhysicalSizeX_DSB = QDoubleSpinBox()
        self.PhysicalSizeX_DSB.setAlignment(Qt.AlignCenter)
        self.PhysicalSizeX_DSB.setMaximum(2147483647.0)
        self.PhysicalSizeX_DSB.setSingleStep(0.001)
        self.PhysicalSizeX_DSB.setDecimals(7)
        self.PhysicalSizeX_DSB.setValue(PhysicalSizeX)
        txt = 'Pixel width:  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.PhysicalSizeX_DSB, row, 1)

        self.PhysicalSizeUnit_CB = QComboBox()
        unitItems = [
            'nm', 'μm', 'mm', 'cm'
        ]
        currentTxt = [unit for unit in unitItems
                      if unit.startswith(PhysicalSizeUnit)]
        self.PhysicalSizeUnit_CB.addItems(unitItems)
        if currentTxt:
            self.PhysicalSizeUnit_CB.setCurrentText(currentTxt[0])
        entriesLayout.addWidget(self.PhysicalSizeUnit_CB, row, 2)
        self.PhysicalSizeUnit_CB.currentTextChanged.connect(self.updatePSUnit)

        row += 1
        self.PhysicalSizeY_DSB = QDoubleSpinBox()
        self.PhysicalSizeY_DSB.setAlignment(Qt.AlignCenter)
        self.PhysicalSizeY_DSB.setMaximum(2147483647.0)
        self.PhysicalSizeY_DSB.setSingleStep(0.001)
        self.PhysicalSizeY_DSB.setDecimals(7)
        self.PhysicalSizeY_DSB.setValue(PhysicalSizeY)
        txt = 'Pixel height:  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.PhysicalSizeY_DSB, row, 1)

        self.PhysicalSizeYUnit_Label = QLabel()
        self.PhysicalSizeYUnit_Label.setStyleSheet(
            'font-size:10pt; padding:5px 0px 2px 0px;'
        )
        unit = self.PhysicalSizeUnit_CB.currentText()
        self.PhysicalSizeYUnit_Label.setText(unit)
        entriesLayout.addWidget(self.PhysicalSizeYUnit_Label, row, 2)

        row += 1
        self.PhysicalSizeZ_DSB = QDoubleSpinBox()
        self.PhysicalSizeZ_DSB.setAlignment(Qt.AlignCenter)
        self.PhysicalSizeZ_DSB.setMaximum(2147483647.0)
        self.PhysicalSizeZ_DSB.setSingleStep(0.001)
        self.PhysicalSizeZ_DSB.setDecimals(7)
        self.PhysicalSizeZ_DSB.setValue(PhysicalSizeZ)
        txt = 'Voxel depth:  '
        self.PSZlabel = QLabel(txt)
        entriesLayout.addWidget(self.PSZlabel, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.PhysicalSizeZ_DSB, row, 1)

        self.PhysicalSizeZUnit_Label = QLabel()
        # padding: top, left, bottom, right
        self.PhysicalSizeZUnit_Label.setStyleSheet(
            'font-size:10pt; padding:5px 0px 2px 0px;'
        )
        unit = self.PhysicalSizeUnit_CB.currentText()
        self.PhysicalSizeZUnit_Label.setText(unit)
        entriesLayout.addWidget(self.PhysicalSizeZUnit_Label, row, 2)

        if SizeZ == 1:
            self.PSZlabel.hide()
            self.PhysicalSizeZ_DSB.hide()
            self.PhysicalSizeZUnit_Label.hide()

        row += 1
        self.SizeC_SB = QSpinBox()
        self.SizeC_SB.setAlignment(Qt.AlignCenter)
        self.SizeC_SB.setMinimum(1)
        self.SizeC_SB.setMaximum(2147483647)
        self.SizeC_SB.setValue(SizeC)
        txt = 'Number of channels:  '
        label = QLabel(txt)
        entriesLayout.addWidget(label, row, 0, alignment=Qt.AlignRight)
        entriesLayout.addWidget(self.SizeC_SB, row, 1)
        self.SizeC_SB.valueChanged.connect(self.addRemoveChannels)

        row += 1
        for j, layout in enumerate(self.channelNameLayouts):
            entriesLayout.addLayout(layout, row, j)

        self.chNames_QLEs = []
        self.saveChannels_QCBs = []
        for c in range(SizeC):
            chName_QLE = QLineEdit()
            chName_QLE.setStyleSheet(
                'background: #FEF9C3'
            )
            chName_QLE.setAlignment(Qt.AlignCenter)
            chName_QLE.textChanged.connect(self.checkChNames)
            if chNames is not None:
                chName_QLE.setText(chNames[c])
            else:
                chName_QLE.setText(f'channel_{c}')

            txt = f'Channel {c} name:  '
            label = QLabel(txt)

            checkBox = QCheckBox('Save this channel')
            checkBox.setChecked(True)
            checkBox.stateChanged.connect(self.saveCh_checkBox_cb)

            self.channelNameLayouts[0].addWidget(label, alignment=Qt.AlignRight)
            self.channelNameLayouts[1].addWidget(chName_QLE)
            self.channelNameLayouts[2].addWidget(checkBox)
            self.chNames_QLEs.append(chName_QLE)
            self.saveChannels_QCBs.append(checkBox)

        self.checkChNames()

        row += 1
        for j, layout in enumerate(self.channelEmWLayouts):
            entriesLayout.addLayout(layout, row, j)

        self.emWavelens_DSBs = []
        for c in range(SizeC):
            row += 1
            emWavelen_DSB = QDoubleSpinBox()
            emWavelen_DSB.setAlignment(Qt.AlignCenter)
            emWavelen_DSB.setMaximum(2147483647.0)
            emWavelen_DSB.setSingleStep(0.001)
            emWavelen_DSB.setDecimals(2)
            if emWavelens is not None:
                emWavelen_DSB.setValue(emWavelens[c])
            else:
                emWavelen_DSB.setValue(500.0)

            txt = f'Channel {c} emission wavelength:  '
            label = QLabel(txt)
            self.channelEmWLayouts[0].addWidget(label, alignment=Qt.AlignRight)
            self.channelEmWLayouts[1].addWidget(emWavelen_DSB)
            self.emWavelens_DSBs.append(emWavelen_DSB)

            unit = QLabel('nm')
            unit.setStyleSheet('font-size:10pt; padding:5px 0px 2px 0px;')
            self.channelEmWLayouts[2].addWidget(unit)

        entriesLayout.setContentsMargins(0, 15, 0, 0)

        if rawDataStruct is None or rawDataStruct==0:
            okButton = QPushButton('Ok')
        elif rawDataStruct==1:
            okButton = QPushButton('Load next position')
        buttonsLayout.addWidget(okButton, 0, 0)

        self.trustButton = None
        self.overWriteButton = None
        if rawDataStruct==1:
            trustButton = QPushButton(
                'Trust metadata reader\n for all next positions')
            trustButton.setToolTip(
                "If you didn't have to manually modify metadata entries\n"
                "it is very likely that metadata from the metadata reader\n"
                "will be correct alsso for all the next positions.\n\n"
                "Click this button to stop showing this dialog and use\n"
                "the metadata from the reader "
                "(except for channel names, I will use the manually entered)"
            )
            buttonsLayout.addWidget(trustButton, 1, 0)
            self.trustButton = trustButton

            overWriteButton = QPushButton(
                'Use the above metadata\nfor all the next positions')
            overWriteButton.setToolTip(
                "If you had to manually modify metadata entries\n"
                "AND you know they will be the same for all next positions\n"
                "you can click this button to stop showing this dialog\n"
                "and use the same metadata for all the next positions."
            )
            buttonsLayout.addWidget(overWriteButton, 1, 1)
            self.overWriteButton = overWriteButton

            trustButton.clicked.connect(self.ok_cb)
            overWriteButton.clicked.connect(self.ok_cb)

        cancelButton = QPushButton('Cancel')
        buttonsLayout.addWidget(cancelButton, 0, 1)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(entriesLayout)
        mainLayout.addLayout(buttonsLayout)
        mainLayout.addStretch(1)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setLayout(mainLayout)
        self.setModal(True)

    def saveCh_checkBox_cb(self, state):
        self.checkChNames()
        idx = self.saveChannels_QCBs.index(self.sender())
        LE = self.chNames_QLEs[idx]
        LE.setDisabled(state==0)
        label = self.channelNameLayouts[0].itemAt(idx).widget()
        if state == 0:
            label.setStyleSheet('color: gray; font-size: 10pt')
        else:
            label.setStyleSheet('color: black; font-size: 10pt')

    def setInvalidChName_StyleSheet(self, LE):
        LE.setStyleSheet(
            'background: #FEF9C3;'
            'border-radius: 4px;'
            'border: 1.5px solid red;'
            'padding: 1px 0px 1px 0px'
        )

    def checkChNames(self, text=''):
        areChNamesValid = True
        if len(self.chNames_QLEs) == 1:
            LE1 = self.chNames_QLEs[0]
            saveCh = self.saveChannels_QCBs[0].isChecked()
            if not saveCh:
                LE1.setStyleSheet('background: #FEF9C3;')
                return areChNamesValid

            s1 = LE1.text()
            if not s1:
                self.setInvalidChName_StyleSheet(LE1)
                areChNamesValid = False
            else:
                LE1.setStyleSheet('background: #FEF9C3;')
            return areChNamesValid

        for LE1, LE2 in combinations(self.chNames_QLEs, 2):
            s1 = LE1.text()
            s2 = LE2.text()
            LE1_idx = self.chNames_QLEs.index(LE1)
            LE2_idx = self.chNames_QLEs.index(LE2)
            saveCh1 = self.saveChannels_QCBs[LE1_idx].isChecked()
            saveCh2 = self.saveChannels_QCBs[LE2_idx].isChecked()
            if not s1 or not s2 or s1==s2:
                if not s1 and saveCh1:
                    self.setInvalidChName_StyleSheet(LE1)
                    areChNamesValid = False
                else:
                    LE1.setStyleSheet('background: #FEF9C3;')
                if not s2 and saveCh2:
                    self.setInvalidChName_StyleSheet(LE2)
                    areChNamesValid = False
                else:
                    LE2.setStyleSheet('background: #FEF9C3;')
                if s1 == s2 and saveCh1 and saveCh2:
                    self.setInvalidChName_StyleSheet(LE1)
                    self.setInvalidChName_StyleSheet(LE2)
                    areChNamesValid = False
            else:
                LE1.setStyleSheet('background: #FEF9C3;')
                LE2.setStyleSheet('background: #FEF9C3;')
        return areChNamesValid

    def hideShowPhysicalSizeZ(self, value):
        if value > 1:
            self.PSZlabel.show()
            self.PhysicalSizeZ_DSB.show()
            self.PhysicalSizeZUnit_Label.show()
        else:
            self.PSZlabel.hide()
            self.PhysicalSizeZ_DSB.hide()
            self.PhysicalSizeZUnit_Label.hide()

    def updatePSUnit(self, unit):
        self.PhysicalSizeYUnit_Label.setText(unit)
        self.PhysicalSizeZUnit_Label.setText(unit)

    def addRemoveChannels(self, value):
        currentSizeC = len(self.chNames_QLEs)
        DeltaChannels = abs(value-currentSizeC)
        if value > currentSizeC:
            for c in range(currentSizeC, currentSizeC+DeltaChannels):
                chName_QLE = QLineEdit()
                chName_QLE.setStyleSheet(
                    'background: #FEF9C3'
                )
                chName_QLE.setAlignment(Qt.AlignCenter)
                chName_QLE.setText(f'channel_{c}')
                chName_QLE.textChanged.connect(self.checkChNames)

                txt = f'Channel {c} name:  '
                label = QLabel(txt)

                checkBox = QCheckBox('Save this channel')
                checkBox.setChecked(True)
                checkBox.stateChanged.connect(self.saveCh_checkBox_cb)

                self.channelNameLayouts[0].addWidget(label, alignment=Qt.AlignRight)
                self.channelNameLayouts[1].addWidget(chName_QLE)
                self.channelNameLayouts[2].addWidget(checkBox)

                self.chNames_QLEs.append(chName_QLE)
                self.saveChannels_QCBs.append(checkBox)

                emWavelen_DSB = QDoubleSpinBox()
                emWavelen_DSB.setAlignment(Qt.AlignCenter)
                emWavelen_DSB.setMaximum(2147483647.0)
                emWavelen_DSB.setSingleStep(0.001)
                emWavelen_DSB.setDecimals(2)
                emWavelen_DSB.setValue(500.0)
                unit = QLabel('nm')
                unit.setStyleSheet('font-size:10pt; padding:5px 0px 2px 0px;')

                txt = f'Channel {c} emission wavelength:  '
                label = QLabel(txt)
                self.channelEmWLayouts[0].addWidget(label, alignment=Qt.AlignRight)
                self.channelEmWLayouts[1].addWidget(emWavelen_DSB)
                self.channelEmWLayouts[2].addWidget(unit)
                self.emWavelens_DSBs.append(emWavelen_DSB)
        else:
            for c in range(currentSizeC, currentSizeC+DeltaChannels):
                label = self.channelNameLayouts[0].itemAt(c-1).widget()
                chName_QLE = self.channelNameLayouts[1].itemAt(c-1).widget()
                checkBox = self.channelNameLayouts[2].itemAt(c-1).widget()
                self.channelNameLayouts[0].removeWidget(label)
                self.channelNameLayouts[1].removeWidget(chName_QLE)
                self.channelNameLayouts[1].removeWidget(checkBox)
                self.chNames_QLEs.pop(-1)
                self.saveChannels_QCBs.pop(-1)

                label = self.channelEmWLayouts[0].itemAt(c-1).widget()
                emWavelen_DSB = self.channelEmWLayouts[1].itemAt(c-1).widget()
                unit = self.channelEmWLayouts[2].itemAt(c-1).widget()
                self.channelEmWLayouts[0].removeWidget(label)
                self.channelEmWLayouts[1].removeWidget(emWavelen_DSB)
                self.channelEmWLayouts[2].removeWidget(unit)
                self.emWavelens_DSBs.pop(-1)

                self.adjustSize()

    def ok_cb(self, event):
        DimensionOrder = self.DimensionOrder_QLE.text()
        m = re.findall('[TZCYXStzcyxs]', DimensionOrder)

        if len(m) != len(DimensionOrder) or not m:
            err_msg = (
                f'"{DimensionOrder}" is not a valid order of dimensions.\n\n'
                f'The letters available are {list("TZCYXS")} without spaces or punctuation.'
                '(e.g. ZYX)'
            )
            msg = QtGui.QMessageBox()
            msg.critical(
               self, 'Invalid order of dimensions', err_msg, msg.Ok
            )
            return

        areChNamesValid = self.checkChNames()
        if not areChNamesValid:
            err_msg = (
                'Channel names cannot be empty or equal to each other.\n\n'
                'Insert a unique text for each channel name'
            )
            msg = QtGui.QMessageBox()
            msg.critical(
               self, 'Invalid channel names', err_msg, msg.Ok
            )
            return

        self.getValues()
        self.convertUnits()

        if self.sender() == self.trustButton:
            self.trust = True
        elif self.sender() == self.overWriteButton:
            self.overWrite = True

        self.cancel = False
        self.close()

    def getValues(self):
        self.LensNA = self.LensNA_DSB.value()
        self.DimensionOrder = self.DimensionOrder_QLE.text()
        self.SizeT = self.SizeT_SB.value()
        self.SizeZ = self.SizeZ_SB.value()
        self.SizeC = self.SizeC_SB.value()
        self.SizeS = self.SizeS_SB.value()
        self.TimeIncrement = self.TimeIncrement_DSB.value()
        self.PhysicalSizeX = self.PhysicalSizeX_DSB.value()
        self.PhysicalSizeY = self.PhysicalSizeY_DSB.value()
        self.PhysicalSizeZ = self.PhysicalSizeZ_DSB.value()
        self.chNames = []
        self.saveChannels = []
        for LE, QCB in zip(self.chNames_QLEs, self.saveChannels_QCBs):
            s = LE.text()
            s = "".join(c if c.isalnum() or c=='_' or c=='' else '_' for c in s)
            trim_ = s.endswith('_')
            while trim_:
                s = s[:-1]
                trim_ = s.endswith('_')
            self.chNames.append(s)
            self.saveChannels.append(QCB.isChecked())
        self.emWavelens = [DSB.value() for DSB in self.emWavelens_DSBs]

    def convertUnits(self):
        timeUnit = self.TimeIncrementUnit_CB.currentText()
        if timeUnit == 'ms':
            self.TimeIncrement /= 1000
        elif timeUnit == 'minutes':
            self.TimeIncrement *= 60
        elif timeUnit == 'hours':
            self.TimeIncrement *= 3600

        PhysicalSizeUnit = self.PhysicalSizeUnit_CB.currentText()
        if timeUnit == 'nm':
            self.PhysicalSizeX /= 1000
            self.PhysicalSizeY /= 1000
            self.PhysicalSizeZ /= 1000
        elif timeUnit == 'mm':
            self.PhysicalSizeX *= 1000
            self.PhysicalSizeY *= 1000
            self.PhysicalSizeZ *= 1000
        elif timeUnit == 'cm':
            self.PhysicalSizeX *= 1e4
            self.PhysicalSizeY *= 1e4
            self.PhysicalSizeZ *= 1e4

    def cancel_cb(self, event):
        self.cancel = True
        self.close()


class QDialogCombobox(QDialog):
    def __init__(self, title, ComboBoxItems, informativeText,
                 CbLabel='Select value:  ', parent=None,
                 defaultChannelName=None, iconPixmap=None):
        self.cancel = True
        self.selectedItemText = ''
        self.selectedItemIdx = None
        super().__init__(parent)
        self.setWindowTitle(title)

        mainLayout = QVBoxLayout()
        infoLayout = QHBoxLayout()
        topLayout = QHBoxLayout()
        bottomLayout = QHBoxLayout()

        if iconPixmap is not None:
            label = QLabel()
            # padding: top, left, bottom, right
            # label.setStyleSheet("padding:5px 0px 10px 0px;")
            label.setPixmap(iconPixmap)
            infoLayout.addWidget(label)

        if informativeText:
            infoLabel = QLabel(informativeText)
            infoLayout.addWidget(infoLabel, alignment=Qt.AlignCenter)

        if CbLabel:
            label = QLabel(CbLabel)
            topLayout.addWidget(label, alignment=Qt.AlignRight)

        combobox = QComboBox()
        combobox.addItems(ComboBoxItems)
        if defaultChannelName is not None and defaultChannelName in ComboBoxItems:
            combobox.setCurrentText(defaultChannelName)
        self.ComboBox = combobox
        topLayout.addWidget(combobox)
        topLayout.setContentsMargins(0, 10, 0, 0)

        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        bottomLayout.addWidget(okButton, alignment=Qt.AlignRight)

        cancelButton = QPushButton('Cancel')
        bottomLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        bottomLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(infoLayout)
        mainLayout.addLayout(topLayout)
        mainLayout.addLayout(bottomLayout)
        self.setLayout(mainLayout)

        self.setModal(True)

        # Connect events
        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.close)


    def ok_cb(self, event):
        self.cancel = False
        self.selectedItemText = self.ComboBox.currentText()
        self.selectedItemIdx = self.ComboBox.currentIndex()
        self.close()


class QDialogListbox(QDialog):
    def __init__(self, title, text, items, cancelText='Cancel',
                 multiSelection=True, parent=None):
        self.cancel = True
        super().__init__(parent)
        self.setWindowTitle(title)

        mainLayout = QVBoxLayout()
        topLayout = QVBoxLayout()
        bottomLayout = QHBoxLayout()

        label = QLabel(text)
        _font = QtGui.QFont()
        _font.setPointSize(10)
        label.setFont(_font)
        # padding: top, left, bottom, right
        label.setStyleSheet("padding:0px 0px 3px 0px;")
        topLayout.addWidget(label, alignment=Qt.AlignCenter)

        listBox = QListWidget()
        listBox.addItems(items)
        if multiSelection:
            listBox.setSelectionMode(QAbstractItemView.ExtendedSelection)
        else:
            listBox.setSelectionMode(QAbstractItemView.SingleSelection)
        listBox.setCurrentRow(0)
        self.listBox = listBox
        topLayout.addWidget(listBox)

        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        bottomLayout.addWidget(okButton, alignment=Qt.AlignRight)

        cancelButton = QPushButton(cancelText)
        # cancelButton.setShortcut(Qt.Key_Escape)
        bottomLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        bottomLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(topLayout)
        mainLayout.addLayout(bottomLayout)
        self.setLayout(mainLayout)

        # Connect events
        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setModal(True)

    def ok_cb(self, event):
        self.cancel = False
        selectedItems = self.listBox.selectedItems()
        self.selectedItemsText = [item.text() for item in selectedItems]
        self.close()

    def cancel_cb(self, event):
        self.cancel = True
        self.selectedItemsText = None
        self.close()

class QDialogAppendTextFilename(QDialog):
    def __init__(self, filename, ext, parent=None, font=None):
        super().__init__(parent)
        self.cancel = True
        filenameNOext, _ = os.path.splitext(filename)
        self.filenameNOext = filenameNOext
        self.ext = ext

        self.setWindowTitle('Append text to file name')

        mainLayout = QVBoxLayout()
        formLayout = QFormLayout()
        buttonsLayout = QHBoxLayout()

        if font is not None:
            self.setFont(font)

        self.LE = QLineEdit()
        self.LE.setAlignment(Qt.AlignCenter)
        formLayout.addRow('Appended text', self.LE)
        self.LE.textChanged.connect(self.updateFinalFilename)

        self.finalName_label = QLabel(
            f'Final file name: "{filenameNOext}_{ext}"'
        )
        # padding: top, left, bottom, right
        self.finalName_label.setStyleSheet(
            'font-size:10pt; padding:5px 0px 0px 0px;'
        )

        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)

        cancelButton = QPushButton('Cancel')

        buttonsLayout.addWidget(okButton, alignment=Qt.AlignRight)
        buttonsLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(formLayout)
        mainLayout.addWidget(self.finalName_label, alignment=Qt.AlignCenter)
        mainLayout.addLayout(buttonsLayout)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.close)

        self.formLayout = formLayout

        self.setLayout(mainLayout)
        self.setModal(True)

    def updateFinalFilename(self, text):
        finalFilename = f'{self.filenameNOext}_{text}{self.ext}'
        self.finalName_label.setText(f'Final file name: "{finalFilename}"')

    def ok_cb(self, event):
        if not self.LE.text():
            err_msg = (
                'Appended name cannot be empty!'
            )
            msg = QtGui.QMessageBox()
            msg.critical(
               self, 'Empty name', err_msg, msg.Ok
            )
            return
        self.cancel = False
        self.close()

class QDialogEntriesWidget(QDialog):
    def __init__(self, entriesLabels, defaultTxts, winTitle='Input',
                 parent=None, font=None):
        self.cancel = True
        self.entriesTxt = []
        self.entriesLabels = entriesLabels
        super().__init__(parent)
        self.setWindowTitle(winTitle)

        mainLayout = QVBoxLayout()
        formLayout = QFormLayout()
        buttonsLayout = QHBoxLayout()

        if font is not None:
            self.setFont(font)

        for label, txt in zip(entriesLabels, defaultTxts):
            LE = QLineEdit()
            LE.setAlignment(Qt.AlignCenter)
            LE.setText(txt)
            formLayout.addRow(label, LE)

        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)

        cancelButton = QPushButton('Cancel')

        buttonsLayout.addWidget(okButton, alignment=Qt.AlignRight)
        buttonsLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(formLayout)
        mainLayout.addLayout(buttonsLayout)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.close)

        self.formLayout = formLayout

        self.setLayout(mainLayout)
        self.setModal(True)

    def ok_cb(self, event):
        self.cancel = False
        self.entriesTxt = [self.formLayout.itemAt(i, 1).widget().text()
                           for i in range(len(self.entriesLabels))]
        self.close()

class QDialogMetadata(QDialog):
    def __init__(self, SizeT, SizeZ, TimeIncrement,
                 PhysicalSizeZ, PhysicalSizeY, PhysicalSizeX,
                 ask_SizeT, ask_TimeIncrement, ask_PhysicalSizes,
                 parent=None, font=None, imgDataShape=None):
        self.cancel = True
        self.ask_TimeIncrement = ask_TimeIncrement
        self.ask_PhysicalSizes = ask_PhysicalSizes
        self.imgDataShape = imgDataShape
        super().__init__(parent)
        self.setWindowTitle('Image properties')

        mainLayout = QVBoxLayout()
        gridLayout = QGridLayout()
        # formLayout = QFormLayout()
        buttonsLayout = QHBoxLayout()

        row = 0
        gridLayout.addWidget(QLabel('Number of frames (SizeT)'), row, 0)
        self.SizeT_SpinBox = QSpinBox()
        self.SizeT_SpinBox.setMinimum(1)
        self.SizeT_SpinBox.setMaximum(2147483647)
        if ask_SizeT:
            self.SizeT_SpinBox.setValue(SizeT)
        else:
            self.SizeT_SpinBox.setValue(1)
            self.SizeT_SpinBox.setDisabled(True)
        self.SizeT_SpinBox.setAlignment(Qt.AlignCenter)
        self.SizeT_SpinBox.valueChanged.connect(self.TimeIncrementShowHide)
        gridLayout.addWidget(self.SizeT_SpinBox, row, 1)

        row += 1
        gridLayout.addWidget(QLabel('Number of z-slices (SizeZ)'), row, 0)
        self.SizeZ_SpinBox = QSpinBox()
        self.SizeZ_SpinBox.setMinimum(1)
        self.SizeZ_SpinBox.setMaximum(2147483647)
        self.SizeZ_SpinBox.setValue(SizeZ)
        self.SizeZ_SpinBox.setAlignment(Qt.AlignCenter)
        self.SizeZ_SpinBox.valueChanged.connect(self.SizeZvalueChanged)
        gridLayout.addWidget(self.SizeZ_SpinBox, row, 1)

        row += 1
        self.TimeIncrementLabel = QLabel('Time interval (s)')
        gridLayout.addWidget(self.TimeIncrementLabel, row, 0)
        self.TimeIncrementSpinBox = QDoubleSpinBox()
        self.TimeIncrementSpinBox.setDecimals(7)
        self.TimeIncrementSpinBox.setMaximum(2147483647.0)
        self.TimeIncrementSpinBox.setValue(TimeIncrement)
        self.TimeIncrementSpinBox.setAlignment(Qt.AlignCenter)
        gridLayout.addWidget(self.TimeIncrementSpinBox, row, 1)

        if SizeT == 1 or not ask_TimeIncrement:
            self.TimeIncrementSpinBox.hide()
            self.TimeIncrementLabel.hide()

        row += 1
        self.PhysicalSizeZLabel = QLabel('Physical Size Z (um/pixel)')
        gridLayout.addWidget(self.PhysicalSizeZLabel, row, 0)
        self.PhysicalSizeZSpinBox = QDoubleSpinBox()
        self.PhysicalSizeZSpinBox.setDecimals(7)
        self.PhysicalSizeZSpinBox.setMaximum(2147483647.0)
        self.PhysicalSizeZSpinBox.setValue(PhysicalSizeZ)
        self.PhysicalSizeZSpinBox.setAlignment(Qt.AlignCenter)
        gridLayout.addWidget(self.PhysicalSizeZSpinBox, row, 1)

        if SizeZ==1 or not ask_PhysicalSizes:
            self.PhysicalSizeZSpinBox.hide()
            self.PhysicalSizeZLabel.hide()

        row += 1
        self.PhysicalSizeYLabel = QLabel('Physical Size Y (um/pixel)')
        gridLayout.addWidget(self.PhysicalSizeYLabel, row, 0)
        self.PhysicalSizeYSpinBox = QDoubleSpinBox()
        self.PhysicalSizeYSpinBox.setDecimals(7)
        self.PhysicalSizeYSpinBox.setMaximum(2147483647.0)
        self.PhysicalSizeYSpinBox.setValue(PhysicalSizeY)
        self.PhysicalSizeYSpinBox.setAlignment(Qt.AlignCenter)
        gridLayout.addWidget(self.PhysicalSizeYSpinBox, row, 1)

        if not ask_PhysicalSizes:
            self.PhysicalSizeYSpinBox.hide()
            self.PhysicalSizeYLabel.hide()

        row += 1
        self.PhysicalSizeXLabel = QLabel('Physical Size X (um/pixel)')
        gridLayout.addWidget(self.PhysicalSizeXLabel, row, 0)
        self.PhysicalSizeXSpinBox = QDoubleSpinBox()
        self.PhysicalSizeXSpinBox.setDecimals(7)
        self.PhysicalSizeXSpinBox.setMaximum(2147483647.0)
        self.PhysicalSizeXSpinBox.setValue(PhysicalSizeX)
        self.PhysicalSizeXSpinBox.setAlignment(Qt.AlignCenter)
        gridLayout.addWidget(self.PhysicalSizeXSpinBox, row, 1)

        if not ask_PhysicalSizes:
            self.PhysicalSizeXSpinBox.hide()
            self.PhysicalSizeXLabel.hide()

        self.SizeZvalueChanged(SizeZ)

        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)

        cancelButton = QPushButton('Cancel')

        buttonsLayout.addWidget(okButton, alignment=Qt.AlignRight)
        buttonsLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        gridLayout.setColumnMinimumWidth(1, 100)
        mainLayout.addLayout(gridLayout)
        # mainLayout.addLayout(formLayout)
        mainLayout.addLayout(buttonsLayout)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setLayout(mainLayout)
        self.setModal(True)

    def SizeZvalueChanged(self, val):
        if val > 1 and self.imgDataShape is not None:
            maxSizeZ = self.imgDataShape[-3]
            self.SizeZ_SpinBox.setMaximum(maxSizeZ)
        else:
            self.SizeZ_SpinBox.setMaximum(2147483647)

        if not self.ask_PhysicalSizes:
            return
        if val > 1:
            self.PhysicalSizeZSpinBox.show()
            self.PhysicalSizeZLabel.show()
        else:
            self.PhysicalSizeZSpinBox.hide()
            self.PhysicalSizeZLabel.hide()

    def TimeIncrementShowHide(self, val):
        if not self.ask_TimeIncrement:
            return
        if val > 1:
            self.TimeIncrementSpinBox.show()
            self.TimeIncrementLabel.show()
        else:
            self.TimeIncrementSpinBox.hide()
            self.TimeIncrementLabel.hide()

    def ok_cb(self, event):
        self.cancel = False
        self.SizeT = self.SizeT_SpinBox.value()
        self.SizeZ = self.SizeZ_SpinBox.value()
        self.TimeIncrement = self.TimeIncrementSpinBox.value()
        self.PhysicalSizeX = self.PhysicalSizeXSpinBox.value()
        self.PhysicalSizeY = self.PhysicalSizeYSpinBox.value()
        self.PhysicalSizeZ = self.PhysicalSizeZSpinBox.value()
        self.close()

    def cancel_cb(self, event):
        self.cancel = True
        self.close()

class gaussBlurDialog(QDialog):
    def __init__(self, mainWindow):
        super().__init__(mainWindow)
        self.cancel = True
        self.mainWindow = mainWindow

        PosData = mainWindow.data[mainWindow.pos_i]
        items = [PosData.filename]
        try:
            items.extend(list(PosData.ol_data_dict.keys()))
        except Exception as e:
            pass

        self.keys = items

        self.setWindowTitle('Gaussian blur sigma')

        mainLayout = QVBoxLayout()
        formLayout = QFormLayout()
        buttonsLayout = QHBoxLayout()

        self.channelsComboBox = QComboBox()
        self.channelsComboBox.addItems(items)
        self.channelsComboBox.setCurrentText(PosData.manualContrastKey)
        mainLayout.addWidget(self.channelsComboBox)

        self.sigmaQDSB = QDoubleSpinBox()
        self.sigmaQDSB.setAlignment(Qt.AlignCenter)
        self.sigmaQDSB.setSingleStep(0.5)
        self.sigmaQDSB.setValue(1.0)
        formLayout.addRow('Gaussian filter sigma:  ', self.sigmaQDSB)
        formLayout.setContentsMargins(0, 10, 0, 10)

        self.sigmaSlider = QSlider(Qt.Horizontal)
        self.sigmaSlider.setMinimum(0)
        self.sigmaSlider.setMaximum(100)
        self.sigmaSlider.setValue(20)
        self.sigma = 1.0
        self.sigmaSlider.setTickPosition(QSlider.TicksBelow)
        self.sigmaSlider.setTickInterval(10)

        self.PreviewCheckBox = QCheckBox("Preview")
        self.PreviewCheckBox.setChecked(True)

        mainLayout.addLayout(formLayout)
        mainLayout.addWidget(self.sigmaSlider)
        mainLayout.addWidget(self.PreviewCheckBox)


        closeButton = QPushButton('Close')

        buttonsLayout.addWidget(closeButton, alignment=Qt.AlignCenter)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(buttonsLayout)

        self.PreviewCheckBox.clicked.connect(self.preview_cb)
        self.sigmaSlider.sliderMoved.connect(self.sigmaSliderMoved)
        self.sigmaQDSB.valueChanged.connect(self.sigmaQDSB_valueChanged)
        self.channelsComboBox.currentTextChanged.connect(self.apply)
        closeButton.clicked.connect(self.close)

        self.setLayout(mainLayout)

        self.apply()

    def preview_cb(self, checked):
        if not checked:
            self.restoreNonFiltered()
            self.mainWindow.updateALLimg(only_ax1=True, updateSharp=False)
        else:
            self.getData()
            self.apply()

    def getData(self):
        PosData = self.mainWindow.data[self.mainWindow.pos_i]
        key = self.channelsComboBox.currentText()
        if key.find(self.mainWindow.user_ch_name) != -1:
            img = self.mainWindow.getImage()
            data = PosData.img_data
        else:
            img = self.mainWindow.getOlImg(key)
            data = PosData.ol_data[key]

        self.img = img
        self.frame_i = PosData.frame_i
        self.segmSizeT = PosData.segmSizeT
        self.imgData = data

    def getFilteredImg(self):
        img = skimage.filters.gaussian(self.img, sigma=self.sigma)
        if self.mainWindow.overlayButton.isChecked():
            key = self.channelsComboBox.currentText()
            img = self.mainWindow.getOverlayImg(fluoData=(img, key),
                                                setImg=False)
        img = self.mainWindow.normalizeIntensities(img)
        return img

    def apply(self):
        self.getData()
        img = self.getFilteredImg()
        if self.PreviewCheckBox.isChecked():
            self.mainWindow.img1.setImage(img)
            h = self.mainWindow.img1.getHistogram()
            self.mainWindow.hist.plot.setData(*h)

    def sigmaQDSB_valueChanged(self, val):
        self.sigma = val
        self.sigmaSlider.sliderMoved.disconnect()
        self.sigmaSlider.setSliderPosition(int(val*20))
        self.sigmaSlider.sliderMoved.connect(self.sigmaSliderMoved)
        self.apply()

    def sigmaSliderMoved(self, intVal):
        self.sigma = intVal/20
        self.sigmaQDSB.valueChanged.disconnect()
        self.sigmaQDSB.setValue(self.sigma)
        self.sigmaQDSB.valueChanged.connect(self.sigmaSliderMoved)
        self.apply()

    def closeEvent(self, event):
        self.mainWindow.gaussBlurAction.setChecked(False)
        self.mainWindow.updateALLimg(only_ax1=True, updateFilters=False)

class edgeDetectionDialog(QDialog):
    def __init__(self, mainWindow):
        super().__init__(mainWindow)
        self.cancel = True
        self.mainWindow = mainWindow

        if mainWindow is not None:
            PosData = self.mainWindow.data[self.mainWindow.pos_i]
            items = [PosData.filename]
        else:
            items = ['test']
        try:
            PosData = self.mainWindow.data[self.mainWindow.pos_i]
            items.extend(list(PosData.ol_data_dict.keys()))
        except Exception as e:
            pass

        self.keys = items

        self.setWindowTitle('Edge detection')

        mainLayout = QVBoxLayout()
        paramsLayout = QGridLayout()
        buttonsLayout = QHBoxLayout()


        channelCBLabel = QLabel('Channel:')
        mainLayout.addWidget(channelCBLabel)
        self.channelsComboBox = QComboBox()
        self.channelsComboBox.addItems(items)
        if mainWindow is not None:
            self.channelsComboBox.setCurrentText(PosData.manualContrastKey)
        if not self.mainWindow.overlayButton.isChecked():
            self.channelsComboBox.setCurrentIndex(0)
        mainLayout.addWidget(self.channelsComboBox)

        row = 0
        sigmaQSLabel = QLabel('Blur:')
        paramsLayout.addWidget(sigmaQSLabel, row, 0)
        row += 1
        self.sigmaValLabel = QLabel('1.00')
        paramsLayout.addWidget(self.sigmaValLabel, row, 1)
        self.sigmaSlider = QSlider(Qt.Horizontal)
        self.sigmaSlider.setMinimum(1)
        self.sigmaSlider.setMaximum(100)
        self.sigmaSlider.setValue(20)
        self.sigma = 1.0
        self.sigmaSlider.setTickPosition(QSlider.TicksBelow)
        self.sigmaSlider.setTickInterval(10)
        paramsLayout.addWidget(self.sigmaSlider, row, 0)

        row += 1
        sharpQSLabel = QLabel('Sharpen:')
        # padding: top, left, bottom, right
        sharpQSLabel.setStyleSheet("font-size:10pt; padding:5px 0px 0px 0px;")
        paramsLayout.addWidget(sharpQSLabel, row, 0)
        row += 1
        self.sharpValLabel = QLabel('5.00')
        paramsLayout.addWidget(self.sharpValLabel, row, 1)
        self.sharpSlider = QSlider(Qt.Horizontal)
        self.sharpSlider.setMinimum(1)
        self.sharpSlider.setMaximum(100)
        self.sharpSlider.setValue(50)
        self.radius = 5.0
        self.sharpSlider.setTickPosition(QSlider.TicksBelow)
        self.sharpSlider.setTickInterval(10)
        paramsLayout.addWidget(self.sharpSlider, row, 0)

        row += 1
        self.PreviewCheckBox = QCheckBox("Preview")
        self.PreviewCheckBox.setChecked(True)
        paramsLayout.addWidget(self.PreviewCheckBox, row, 0, 1, 2,
                               alignment=Qt.AlignCenter)


        closeButton = QPushButton('Close')

        buttonsLayout.addWidget(closeButton, alignment=Qt.AlignCenter)

        paramsLayout.setContentsMargins(0, 10, 0, 0)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(paramsLayout)
        mainLayout.addLayout(buttonsLayout)

        self.PreviewCheckBox.clicked.connect(self.preview_cb)
        self.sigmaSlider.sliderMoved.connect(self.sigmaSliderMoved)
        self.sharpSlider.sliderMoved.connect(self.sharpSliderMoved)
        self.channelsComboBox.currentTextChanged.connect(self.apply)
        closeButton.clicked.connect(self.close)

        self.setLayout(mainLayout)
        self.apply()

    def setSize(self):
        x = self.pos().x()
        y = self.pos().y()
        h = self.size().height()
        self.setGeometry(x, y, 300, h)

    def preview_cb(self, checked):
        if not checked:
            self.restoreNonFiltered()
            self.mainWindow.updateALLimg(only_ax1=True, updateSharp=False)
        else:
            self.getData()
            self.apply()

    def getData(self):
        key = self.channelsComboBox.currentText()
        PosData = self.mainWindow.data[self.mainWindow.pos_i]
        if key.find(self.mainWindow.user_ch_name) != -1:
            img = self.mainWindow.getImage(normalizeIntens=False)
            data = PosData.img_data
        else:
            img = self.mainWindow.getOlImg(key, normalizeIntens=False)
            data = PosData.ol_data[key]

        if self.PreviewCheckBox.isChecked():
            self.img = skimage.exposure.equalize_adapthist(img)
            self.detectEdges()
        self.frame_i = PosData.frame_i
        self.imgData = data

    def detectEdges(self):
        self.edge = skimage.filters.sobel(self.img)

    def getFilteredImg(self):
        img = self.edge.copy()
        # Blur
        img = skimage.filters.gaussian(img, sigma=self.sigma)
        # Sharpen
        img = img - skimage.filters.gaussian(img, sigma=self.radius)
        if self.mainWindow.overlayButton.isChecked():
            key = self.channelsComboBox.currentText()
            img = self.mainWindow.getOverlayImg(fluoData=(img, key),
                                                setImg=False)
        img = self.mainWindow.normalizeIntensities(img)
        return img


    def apply(self):
        self.getData()
        img = self.getFilteredImg()
        if self.PreviewCheckBox.isChecked():
            self.mainWindow.img1.setImage(img)
            h = self.mainWindow.img1.getHistogram()
            self.mainWindow.hist.plot.setData(*h)

    def sigmaSliderMoved(self, intVal):
        self.sigma = intVal/20
        self.sigmaValLabel.setText(f'{self.sigma:.2f}')
        self.apply()

    def sharpSliderMoved(self, intVal):
        self.radius = 10 - intVal/10
        if self.radius < 0.15:
            self.radius = 0.15
        self.sharpValLabel.setText(f'{intVal/10:.2f}')
        self.apply()

    def closeEvent(self, event):
        self.mainWindow.edgeDetectorAction.setChecked(False)
        self.mainWindow.updateALLimg(only_ax1=True, updateFilters=False)


class entropyFilterDialog(QDialog):
    def __init__(self, mainWindow):
        super().__init__(mainWindow)
        self.cancel = True
        self.mainWindow = mainWindow

        if mainWindow is not None:
            PosData = self.mainWindow.data[self.mainWindow.pos_i]
            items = [PosData.filename]
        else:
            items = ['test']
        try:
            PosData = self.mainWindow.data[self.mainWindow.pos_i]
            items.extend(list(PosData.ol_data_dict.keys()))
        except Exception as e:
            pass

        self.keys = items

        self.setWindowTitle('Edge detection')

        mainLayout = QVBoxLayout()
        paramsLayout = QGridLayout()
        buttonsLayout = QHBoxLayout()


        channelCBLabel = QLabel('Channel:')
        mainLayout.addWidget(channelCBLabel)
        self.channelsComboBox = QComboBox()
        self.channelsComboBox.addItems(items)
        if mainWindow is not None:
            self.channelsComboBox.setCurrentText(PosData.manualContrastKey)
        mainLayout.addWidget(self.channelsComboBox)

        row = 0
        sigmaQSLabel = QLabel('Radius: ')
        paramsLayout.addWidget(sigmaQSLabel, row, 0)
        row += 1
        self.radiusValLabel = QLabel('10')
        paramsLayout.addWidget(self.radiusValLabel, row, 1)
        self.radiusSlider = QSlider(Qt.Horizontal)
        self.radiusSlider.setMinimum(1)
        self.radiusSlider.setMaximum(100)
        self.radiusSlider.setValue(10)
        self.radiusSlider.setTickPosition(QSlider.TicksBelow)
        self.radiusSlider.setTickInterval(10)
        paramsLayout.addWidget(self.radiusSlider, row, 0)

        row += 1
        self.PreviewCheckBox = QCheckBox("Preview")
        self.PreviewCheckBox.setChecked(True)
        paramsLayout.addWidget(self.PreviewCheckBox, row, 0, 1, 2,
                               alignment=Qt.AlignCenter)

        closeButton = QPushButton('Close')

        buttonsLayout.addWidget(closeButton, alignment=Qt.AlignCenter)

        paramsLayout.setContentsMargins(0, 10, 0, 0)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(paramsLayout)
        mainLayout.addLayout(buttonsLayout)

        self.PreviewCheckBox.clicked.connect(self.preview_cb)
        self.radiusSlider.sliderMoved.connect(self.radiusSliderMoved)
        self.channelsComboBox.currentTextChanged.connect(self.apply)
        closeButton.clicked.connect(self.close)

        self.setLayout(mainLayout)

        self.apply()

    def setSize(self):
        x = self.pos().x()
        y = self.pos().y()
        h = self.size().height()
        self.setGeometry(x, y, 300, h)

    def preview_cb(self, checked):
        if not checked:
            self.restoreNonFiltered()
            self.mainWindow.updateALLimg(only_ax1=True, updateSharp=False)
        else:
            self.getData()
            self.apply()

    def getData(self):
        key = self.channelsComboBox.currentText()
        PosData = self.mainWindow.data[self.mainWindow.pos_i]
        if key.find(self.mainWindow.user_ch_name) != -1:
            img = self.mainWindow.getImage()
            data = PosData.img_data
        else:
            img = self.mainWindow.getOlImg(key)
            data = PosData.ol_data[key]
        self.img = skimage.img_as_ubyte(img)
        self.frame_i = PosData.frame_i
        self.imgData = data

    def getFilteredImg(self):
        radius = self.radiusSlider.sliderPosition()
        selem = skimage.morphology.disk(radius)
        entropyImg = skimage.filters.rank.entropy(self.img, selem)
        if self.mainWindow.overlayButton.isChecked():
            key = self.channelsComboBox.currentText()
            img = self.mainWindow.getOverlayImg(fluoData=(entropyImg, key),
                                                setImg=False)
        img = self.mainWindow.normalizeIntensities(entropyImg)
        return img

    def apply(self):
        self.getData()
        img = self.getFilteredImg()
        if self.PreviewCheckBox.isChecked():
            self.mainWindow.img1.setImage(img)
            h = self.mainWindow.img1.getHistogram()
            self.mainWindow.hist.plot.setData(*h)

    def radiusSliderMoved(self, intVal):
        self.radiusValLabel.setText(f'{intVal}')
        self.apply()

    def closeEvent(self, event):
        self.mainWindow.entropyFilterAction.setChecked(False)
        self.mainWindow.updateALLimg(only_ax1=True, updateFilters=False)

class randomWalkerDialog(QDialog):
    def __init__(self, mainWindow):
        super().__init__(mainWindow)
        self.cancel = True
        self.mainWindow = mainWindow

        if mainWindow is not None:
            PosData = self.mainWindow.data[self.mainWindow.pos_i]
            items = [PosData.filename]
        else:
            items = ['test']
        try:
            PosData = self.mainWindow.data[self.mainWindow.pos_i]
            items.extend(list(PosData.ol_data_dict.keys()))
        except Exception as e:
            pass

        self.keys = items

        self.setWindowTitle('Random walker segmentation')

        self.colors = [self.mainWindow.RWbkgrColor,
                       self.mainWindow.RWforegrColor]

        mainLayout = QVBoxLayout()
        paramsLayout = QGridLayout()
        buttonsLayout = QHBoxLayout()

        self.mainWindow.clearAllItems()

        row = 0
        paramsLayout.addWidget(QLabel('Background threshold:'), row, 0)
        row += 1
        self.bkgrThreshValLabel = QLabel('0.05')
        paramsLayout.addWidget(self.bkgrThreshValLabel, row, 1)
        self.bkgrThreshSlider = QSlider(Qt.Horizontal)
        self.bkgrThreshSlider.setMinimum(1)
        self.bkgrThreshSlider.setMaximum(100)
        self.bkgrThreshSlider.setValue(5)
        self.bkgrThreshSlider.setTickPosition(QSlider.TicksBelow)
        self.bkgrThreshSlider.setTickInterval(10)
        paramsLayout.addWidget(self.bkgrThreshSlider, row, 0)

        row += 1
        foregrQSLabel = QLabel('Foreground threshold:')
        # padding: top, left, bottom, right
        foregrQSLabel.setStyleSheet("font-size:10pt; padding:5px 0px 0px 0px;")
        paramsLayout.addWidget(foregrQSLabel, row, 0)
        row += 1
        self.foregrThreshValLabel = QLabel('0.95')
        paramsLayout.addWidget(self.foregrThreshValLabel, row, 1)
        self.foregrThreshSlider = QSlider(Qt.Horizontal)
        self.foregrThreshSlider.setMinimum(1)
        self.foregrThreshSlider.setMaximum(100)
        self.foregrThreshSlider.setValue(95)
        self.foregrThreshSlider.setTickPosition(QSlider.TicksBelow)
        self.foregrThreshSlider.setTickInterval(10)
        paramsLayout.addWidget(self.foregrThreshSlider, row, 0)

        # Parameters link label
        row += 1
        url1 = 'https://scikit-image.org/docs/dev/auto_examples/segmentation/plot_random_walker_segmentation.html'
        url2 = 'https://scikit-image.org/docs/dev/api/skimage.segmentation.html#skimage.segmentation.random_walker'
        htmlTxt1 = f'<a href=\"{url1}">here</a>'
        htmlTxt2 = f'<a href=\"{url2}">here</a>'
        seeHereLabel = QLabel()
        seeHereLabel.setText(f'See {htmlTxt1} and {htmlTxt2} for details '
                              'about Random walker segmentation.')
        seeHereLabel.setTextFormat(Qt.RichText)
        seeHereLabel.setTextInteractionFlags(Qt.TextBrowserInteraction)
        seeHereLabel.setOpenExternalLinks(True)
        font = QtGui.QFont()
        font.setPointSize(10)
        seeHereLabel.setFont(font)
        seeHereLabel.setStyleSheet("padding:10px 0px 0px 0px;")
        paramsLayout.addWidget(seeHereLabel, row, 0, 1, 2)

        computeButton = QPushButton('Compute segmentation')
        closeButton = QPushButton('Close')

        buttonsLayout.addWidget(computeButton, alignment=Qt.AlignRight)
        buttonsLayout.addWidget(closeButton, alignment=Qt.AlignLeft)

        paramsLayout.setContentsMargins(0, 10, 0, 0)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(paramsLayout)
        mainLayout.addLayout(buttonsLayout)

        self.bkgrThreshSlider.sliderMoved.connect(self.bkgrSliderMoved)
        self.foregrThreshSlider.sliderMoved.connect(self.foregrSliderMoved)
        computeButton.clicked.connect(self.computeSegmAndPlot)
        closeButton.clicked.connect(self.close)

        self.setLayout(mainLayout)

        self.getImage()
        self.plotMarkers()

    def getImage(self):
        img = self.mainWindow.getDisplayedCellsImg()
        self.img = img/img.max()
        self.imgRGB = (skimage.color.gray2rgb(self.img)*255).astype(np.uint8)

    def setSize(self):
        x = self.pos().x()
        y = self.pos().y()
        h = self.size().height()
        w = self.size().width()
        if w < 400:
            w = 400
        self.setGeometry(x, y, w, h)

    def plotMarkers(self):
        imgMin, imgMax = self.computeMarkers()

        img = self.img

        imgRGB = self.imgRGB.copy()
        R, G, B = self.colors[0]
        imgRGB[:, :, 0][img < imgMin] = R
        imgRGB[:, :, 1][img < imgMin] = G
        imgRGB[:, :, 2][img < imgMin] = B
        R, G, B = self.colors[1]
        imgRGB[:, :, 0][img > imgMax] = R
        imgRGB[:, :, 1][img > imgMax] = G
        imgRGB[:, :, 2][img > imgMax] = B

        self.mainWindow.img1.setImage(imgRGB)

    def computeMarkers(self):
        bkgrThresh = self.bkgrThreshSlider.sliderPosition()/100
        foregrThresh = self.foregrThreshSlider.sliderPosition()/100
        img = self.img
        self.markers = np.zeros(img.shape, np.uint8)
        imgRange = img.max() - img.min()
        imgMin = img.min() + imgRange*bkgrThresh
        imgMax = img.min() + imgRange*foregrThresh
        self.markers[img < imgMin] = 1
        self.markers[img > imgMax] = 2
        return imgMin, imgMax

    def computeSegm(self, checked=True):
        self.mainWindow.storeUndoRedoStates(False)
        self.mainWindow.titleLabel.setText(
            'Randomly walking around... ', color='w')
        img = self.img
        img = skimage.exposure.rescale_intensity(img)
        t0 = time.time()
        lab = skimage.segmentation.random_walker(img, self.markers, mode='bf')
        lab = skimage.measure.label(lab>1)
        t1 = time.time()
        if len(np.unique(lab)) > 2:
            skimage.morphology.remove_small_objects(lab, min_size=5,
                                                    in_place=True)
        PosData = self.mainWindow.data[self.mainWindow.pos_i]
        PosData.lab = lab
        return t1-t0

    def computeSegmAndPlot(self):
        deltaT = self.computeSegm()

        PosData = self.mainWindow.data[self.mainWindow.pos_i]

        self.mainWindow.update_rp()
        self.mainWindow.tracking(enforce=True)
        self.mainWindow.updateALLimg()
        self.mainWindow.warnEditingWithCca_df('Random Walker segmentation')
        txt = f'Random Walker segmentation computed in {deltaT:.3f} s'
        print('-----------------')
        print(txt)
        print('=================')
        # self.mainWindow.titleLabel.setText(txt, color='g')

    def bkgrSliderMoved(self, intVal):
        self.bkgrThreshValLabel.setText(f'{intVal/100:.2f}')
        self.plotMarkers()

    def foregrSliderMoved(self, intVal):
        self.foregrThreshValLabel.setText(f'{intVal/100:.2f}')
        self.plotMarkers()

    def closeEvent(self, event):
        self.mainWindow.segmModel = ''
        self.mainWindow.updateALLimg()

class FutureFramesAction_QDialog(QDialog):
    def __init__(self, frame_i, last_tracked_i, change_txt,
                 applyTrackingB=False, parent=None):
        self.decision = None
        self.last_tracked_i = last_tracked_i
        super().__init__(parent)
        self.setWindowTitle('Future frames action?')

        mainLayout = QVBoxLayout()
        txtLayout = QVBoxLayout()
        doNotShowLayout = QVBoxLayout()
        buttonsLayout = QVBoxLayout()

        txt = (
            'You already visited/checked future frames '
            f'{frame_i+1}-{last_tracked_i}.\n\n'
            f'The requested "{change_txt}" change might result in\n'
            'NON-correct segmentation/tracking for those frames.\n'
        )

        txtLabel = QLabel(txt)
        _font = QtGui.QFont()
        _font.setPointSize(10)
        _font.setBold(True)
        txtLabel.setFont(_font)
        txtLabel.setAlignment(Qt.AlignCenter)
        # padding: top, left, bottom, right
        txtLabel.setStyleSheet("padding:0px 0px 3px 0px;")
        txtLayout.addWidget(txtLabel, alignment=Qt.AlignCenter)

        infoTxt = (
           f'  Choose one of the following options:\n\n'
           f'      1.  Apply the "{change_txt}" only to this frame and re-initialize\n'
            '          the future frames to the segmentation file present\n'
            '          on the hard drive.\n'
            '      2.  Apply only to this frame and keep the future frames as they are.\n'
            '      3.  Apply the change to ALL visited/checked future frames.\n'
            # '      4.  Apply the change to a specific range of future frames.\n'

        )

        if applyTrackingB:
            infoTxt = (
                f'{infoTxt}'
                '4. Repeat ONLY tracking for all future frames (RECOMMENDED)'
            )

        infotxtLabel = QLabel(infoTxt)
        _font = QtGui.QFont()
        _font.setPointSize(10)
        infotxtLabel.setFont(_font)

        infotxtLabel.setStyleSheet("padding:0px 0px 3px 0px;")
        txtLayout.addWidget(infotxtLabel, alignment=Qt.AlignCenter)

        noteTxt = (
            'NOTE: Only changes applied to current frame can be undone.\n'
            '      Changes applied to future frames CANNOT be UNDONE!\n'
        )

        noteTxtLabel = QLabel(noteTxt)
        _font = QtGui.QFont()
        _font.setPointSize(10)
        _font.setBold(True)
        noteTxtLabel.setFont(_font)
        # padding: top, left, bottom, right
        noteTxtLabel.setStyleSheet("padding:0px 0px 3px 0px;")
        txtLayout.addWidget(noteTxtLabel, alignment=Qt.AlignCenter)

        # Do not show this message again checkbox
        doNotShowCheckbox = QCheckBox(
            'Remember my choice and do not show this message again')
        doNotShowLayout.addWidget(doNotShowCheckbox)
        doNotShowLayout.setContentsMargins(50, 0, 0, 10)
        self.doNotShowCheckbox = doNotShowCheckbox

        apply_and_reinit_b = QPushButton(
                    'Apply only to this frame and re-initialize future frames')

        self.apply_and_reinit_b = apply_and_reinit_b
        buttonsLayout.addWidget(apply_and_reinit_b)

        apply_and_NOTreinit_b = QPushButton(
                'Apply only to this frame and keep future frames as they are')
        self.apply_and_NOTreinit_b = apply_and_NOTreinit_b
        buttonsLayout.addWidget(apply_and_NOTreinit_b)

        apply_to_all_b = QPushButton(
                    'Apply to all future frames')
        self.apply_to_all_b = apply_to_all_b
        buttonsLayout.addWidget(apply_to_all_b)

        self.applyTrackingButton = None
        if applyTrackingB:
            applyTrackingButton = QPushButton(
                        'Repeat ONLY tracking for all future frames')
            self.applyTrackingButton = applyTrackingButton
            buttonsLayout.addWidget(applyTrackingButton)

        apply_to_range_b = QPushButton(
                    'Apply only to a range of future frames')
        self.apply_to_range_b = apply_to_range_b
        # buttonsLayout.addWidget(apply_to_range_b)

        buttonsLayout.setContentsMargins(20, 0, 20, 0)

        self.formLayout = QFormLayout()
        self.OkRangeLayout = QVBoxLayout()
        self.OkRangeButton = QPushButton('Ok')
        self.OkRangeLayout.addWidget(self.OkRangeButton)

        ButtonsGroup = QButtonGroup(self)
        ButtonsGroup.addButton(apply_and_reinit_b)
        ButtonsGroup.addButton(apply_and_NOTreinit_b)
        if applyTrackingB:
            ButtonsGroup.addButton(applyTrackingButton)
        ButtonsGroup.addButton(apply_to_all_b)
        ButtonsGroup.addButton(apply_to_range_b)
        ButtonsGroup.addButton(self.OkRangeButton)

        mainLayout.addLayout(txtLayout)
        mainLayout.addLayout(doNotShowLayout)
        mainLayout.addLayout(buttonsLayout)
        mainLayout.addLayout(self.formLayout)
        self.mainLayout = mainLayout
        self.setLayout(mainLayout)

        # Connect events
        ButtonsGroup.buttonClicked.connect(self.buttonClicked)

        self.setModal(True)

    def buttonClicked(self, button):
        if button == self.apply_and_reinit_b:
            self.decision = 'apply_and_reinit'
            self.endFrame_i = None
            self.close()
        elif button == self.apply_and_NOTreinit_b:
            self.decision = 'apply_and_NOTreinit'
            self.endFrame_i = None
            self.close()
        elif button == self.apply_to_all_b:
            self.decision = 'apply_to_all'
            self.endFrame_i = self.last_tracked_i
            self.close()
        elif button == self.applyTrackingButton:
            self.decision = 'only_tracking'
            self.endFrame_i = self.last_tracked_i
            self.close()
        elif button == self.apply_to_range_b:
            endFrame_LineEntry = QLineEdit()
            self.formLayout.addRow('Apply until frame: ',
                                   endFrame_LineEntry)
            endFrame_LineEntry.setText(f'{self.last_tracked_i}')
            endFrame_LineEntry.setAlignment(Qt.AlignCenter)
            self.formLayout.setContentsMargins(100, 10, 100, 0)

            self.mainLayout.addLayout(self.OkRangeLayout)
            self.OkRangeLayout.setContentsMargins(150, 0, 150, 0)

            self.endRangeFrame_i = int(endFrame_LineEntry.text())
        elif button == self.OkRangeButton:
            self.decision = 'apply_to_range'
            self.endFrame_i = self.endRangeFrame_i
            self.close()


class nonModalTempQMessage(QWidget):
    def __init__(self, msg='Doing stuff...', parent=None):
        super().__init__(parent)

        layout = QVBoxLayout()

        msgLabel = QLabel(msg)
        _font = QtGui.QFont()
        _font.setPointSize(10)
        _font.setBold(True)
        msgLabel.setFont(_font)
        msgLabel.setAlignment(Qt.AlignCenter)
        layout.addWidget(msgLabel, alignment=Qt.AlignCenter)

        self.setLayout(layout)


class CellsSlideshow_GUI(QMainWindow):
    """Main Window."""

    def __init__(self, parent=None, PosData=None, button_toUncheck=None,
                       Left=50, Top=50, spinBox=None):
        self.button_toUncheck = button_toUncheck
        self.parent = parent
        self.PosData = PosData
        self.spinBox = spinBox
        """Initializer."""
        super().__init__(parent)
        self.setGeometry(Left, Top, 850, 800)

        self.gui_createActions()
        self.gui_createMenuBar()
        self.gui_createToolBars()

        self.gui_createStatusBar()

        self.gui_createGraphics()

        self.gui_connectImgActions()

        self.gui_createImgWidgets()
        self.gui_connectActions()

        mainContainer = QtGui.QWidget()
        self.setCentralWidget(mainContainer)

        mainLayout = QtGui.QGridLayout()
        mainLayout.addWidget(self.graphLayout, 0, 0, 1, 1)
        mainLayout.addLayout(self.img_Widglayout, 1, 0)

        mainContainer.setLayout(mainLayout)

        if PosData is None:
            PosData = self.parent.data[self.parent.pos_i]

        self.frame_i = PosData.frame_i
        self.num_frames = PosData.SizeT
        self.setWindowTitle(f"Cell-ACDC - {PosData.relPath}")

    def gui_createActions(self):
        # File actions
        self.exitAction = QAction("&Exit", self)

        # Toolbar actions
        self.prevAction = QAction(QIcon(":arrow-left.svg"),
                                        "Previous frame", self)
        self.nextAction = QAction(QIcon(":arrow-right.svg"),
                                        "Next Frame", self)
        self.jumpForwardAction = QAction(QIcon(":arrow-up.svg"),
                                        "Jump to 10 frames ahead", self)
        self.jumpBackwardAction = QAction(QIcon(":arrow-down.svg"),
                                        "Jump to 10 frames back", self)
        self.prevAction.setShortcut("left")
        self.nextAction.setShortcut("right")
        self.jumpForwardAction.setShortcut("up")
        self.jumpBackwardAction.setShortcut("down")

    def gui_createMenuBar(self):
        menuBar = self.menuBar()
        # File menu
        fileMenu = QMenu("&File", self)
        menuBar.addMenu(fileMenu)
        # fileMenu.addAction(self.newAction)
        fileMenu.addAction(self.exitAction)

    def gui_createToolBars(self):
        toolbarSize = 30

        editToolBar = QToolBar("Edit", self)
        editToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        self.addToolBar(editToolBar)

        editToolBar.addAction(self.prevAction)
        editToolBar.addAction(self.nextAction)
        editToolBar.addAction(self.jumpBackwardAction)
        editToolBar.addAction(self.jumpForwardAction)

    def gui_connectActions(self):
        self.exitAction.triggered.connect(self.close)
        self.prevAction.triggered.connect(self.prev_frame)
        self.nextAction.triggered.connect(self.next_frame)
        self.jumpForwardAction.triggered.connect(self.skip10ahead_frames)
        self.jumpBackwardAction.triggered.connect(self.skip10back_frames)

    def gui_createStatusBar(self):
        self.statusbar = self.statusBar()
        # Temporary message
        self.statusbar.showMessage("Ready", 3000)
        # Permanent widget
        self.wcLabel = QLabel(f"")
        self.statusbar.addPermanentWidget(self.wcLabel)

    def gui_createGraphics(self):
        self.graphLayout = pg.GraphicsLayoutWidget()

        # Plot Item container for image
        self.Plot = pg.PlotItem()
        self.Plot.invertY(True)
        self.Plot.setAspectLocked(True)
        self.Plot.hideAxis('bottom')
        self.Plot.hideAxis('left')
        self.graphLayout.addItem(self.Plot, row=1, col=1)

        # Image Item
        self.img = pg.ImageItem(np.zeros((512,512)))
        self.Plot.addItem(self.img)

        #Image histogram
        hist = pg.HistogramLUTItem()
        self.hist = hist
        hist.setImageItem(self.img)
        self.graphLayout.addItem(hist, row=1, col=0)

        # Current frame text
        self.frameLabel = pg.LabelItem(justify='center', color='w', size='14pt')
        self.frameLabel.setText(' ')
        self.graphLayout.addItem(self.frameLabel, row=2, col=0, colspan=2)

    def gui_connectImgActions(self):
        self.img.hoverEvent = self.gui_hoverEventImg

    def gui_createImgWidgets(self):
        if self.PosData is None:
            PosData = self.parent.data[self.parent.pos_i]
        else:
            PosData = self.PosData
        self.img_Widglayout = QtGui.QGridLayout()

        # Frames scrollbar
        self.framesScrollBar = QScrollBar(Qt.Horizontal)
        self.framesScrollBar.setFixedHeight(20)
        self.framesScrollBar.setMinimum(1)
        self.framesScrollBar.setMaximum(PosData.SizeT)
        t_label = QLabel('frame  ')
        _font = QtGui.QFont()
        _font.setPointSize(10)
        t_label.setFont(_font)
        self.img_Widglayout.addWidget(
                t_label, 0, 0, alignment=Qt.AlignRight)
        self.img_Widglayout.addWidget(
                self.framesScrollBar, 0, 1, 1, 20)
        self.framesScrollBar.sliderMoved.connect(self.framesScrollBarMoved)

        # z-slice scrollbar
        self.zSliceScrollBar = QScrollBar(Qt.Horizontal)
        self.zSliceScrollBar.setFixedHeight(20)
        self.zSliceScrollBar.setDisabled(True)
        _z_label = QLabel('z-slice  ')
        _font = QtGui.QFont()
        _font.setPointSize(10)
        _z_label.setFont(_font)
        self.z_label = _z_label
        self.img_Widglayout.addWidget(_z_label, 1, 0, alignment=Qt.AlignCenter)
        self.img_Widglayout.addWidget(self.zSliceScrollBar, 1, 1, 1, 20)

        self.img_Widglayout.setContentsMargins(100, 0, 50, 0)

        self.zSliceScrollBar.sliderMoved.connect(self.update_z_slice)

    def framesScrollBarMoved(self, frame_n):
        self.frame_i = frame_n-1
        if self.spinBox is not None:
            self.spinBox.setValue(frame_n)
        self.update_img()

    def gui_hoverEventImg(self, event):
        # Update x, y, value label bottom right
        try:
            x, y = event.pos()
            xdata, ydata = int(x), int(y)
            _img = self.img.image
            Y, X = _img.shape
            if xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y:
                val = _img[ydata, xdata]
                self.wcLabel.setText(f'(x={x:.2f}, y={y:.2f}, value={val:.2f})')
            else:
                self.wcLabel.setText(f'')
        except Exception as e:
            self.wcLabel.setText(f'')

    def next_frame(self):
        if self.frame_i < self.num_frames-1:
            self.frame_i += 1
        else:
            self.frame_i = 0
        self.update_img()

    def prev_frame(self):
        if self.frame_i > 0:
            self.frame_i -= 1
        else:
            self.frame_i = self.num_frames-1
        self.update_img()

    def skip10ahead_frames(self):
        if self.frame_i < self.num_frames-10:
            self.frame_i += 10
        else:
            self.frame_i = 0
        self.update_img()

    def skip10back_frames(self):
        if self.frame_i > 9:
            self.frame_i -= 10
        else:
            self.frame_i = self.num_frames-1
        self.update_img()

    def update_z_slice(self, z):
        if self.PosData is None:
            PosData = self.parent.data[self.parent.pos_i]
        else:
            PosData = self.PosData
            idx = (PosData.filename, PosData.frame_i)
            PosData.segmInfo_df.at[idx, 'z_slice_used_gui'] = z
        self.update_img()

    def getImage(self):
        PosData = self.PosData
        frame_i = self.frame_i
        if PosData.SizeZ > 1:
            idx = (PosData.filename, frame_i)
            z = PosData.segmInfo_df.at[idx, 'z_slice_used_gui']
            zProjHow = PosData.segmInfo_df.at[idx, 'which_z_proj_gui']
            img = PosData.img_data[frame_i]
            if zProjHow == 'single z-slice':
                self.zSliceScrollBar.setSliderPosition(z)
                self.z_label.setText(f'z-slice  {z+1:02}/{PosData.SizeZ}')
                img = img[z].copy()
            elif zProjHow == 'max z-projection':
                img = img.max(axis=0).copy()
            elif zProjHow == 'mean z-projection':
                img = img.mean(axis=0).copy()
            elif zProjHow == 'median z-proj.':
                img = np.median(img, axis=0).copy()
        else:
            img = PosData.img_data[frame_i].copy()
        return img


    def update_img(self):
        self.frameLabel.setText(
                 f'Current frame = {self.frame_i+1}/{self.num_frames}')
        if self.parent is None:
            img = self.getImage()
        else:
            img = self.parent.getImage(frame_i=self.frame_i)
        self.img.setImage(img)
        self.framesScrollBar.setSliderPosition(self.frame_i+1)

    def closeEvent(self, event):
        if self.button_toUncheck is not None:
            self.button_toUncheck.setChecked(False)

class cellpose_ParamsDialog(QDialog):
    def __init__(self, parent=None):
        self.cancel = True
        super().__init__(parent)
        self.setWindowTitle("Cellpose parameters")
        if parent is None:
            self.setWindowIcon(QIcon(":assign-motherbud.svg"))


        mainLayout = QVBoxLayout()
        entriesLayout = QGridLayout()

        row = 0
        diameterLabel = QLabel('Diameter of cell (pixels):')
        diameterEntry = QSpinBox()
        diameterEntry.setValue(0)
        diameterEntry.setAlignment(Qt.AlignCenter)
        entriesLayout.addWidget(diameterLabel, row, 0)
        row += 1
        entriesLayout.addWidget(diameterEntry, row, 0, 1, 2)
        self.diameterEntry = diameterEntry

        row += 1
        flowThreshLabel = QLabel('Flow threshold: ')
        flowThreshSlider = QSlider(Qt.Horizontal)
        flowThreshSlider.setMinimum(0)
        flowThreshSlider.setMaximum(10)
        flowThreshSlider.setValue(4)
        entriesLayout.addWidget(flowThreshLabel, row, 0)
        row += 1
        entriesLayout.addWidget(flowThreshSlider, row, 0)
        self.flowThreshLabelValue = QLabel('0.4')
        entriesLayout.addWidget(self.flowThreshLabelValue, row, 1)
        self.flowThreshSlider = flowThreshSlider
        self.flowThreshSlider.sliderMoved.connect(self.updateFlowThreshVal)

        row += 1
        cellProbThreshLabel = QLabel('Cell probability threshold: ')
        cellProbThreshSlider = QSlider(Qt.Horizontal)
        cellProbThreshSlider.setMinimum(-6)
        cellProbThreshSlider.setMaximum(6)
        cellProbThreshSlider.setValue(0)
        entriesLayout.addWidget(cellProbThreshLabel, row, 0)
        row += 1
        entriesLayout.addWidget(cellProbThreshSlider, row, 0)
        self.cellProbThreshLabelValue = QLabel('0')
        entriesLayout.addWidget(self.cellProbThreshLabelValue, row, 1)
        self.cellProbThreshSlider = cellProbThreshSlider
        self.cellProbThreshSlider.sliderMoved.connect(self.updateCellProbVal)

        row += 1
        minSizeLabel = QLabel("Remove objects smaller than (area in pixels):")
        entriesLayout.addWidget(minSizeLabel, row, 0)
        row += 1
        minSize_SB = QSpinBox()
        minSize_SB.setAlignment(Qt.AlignCenter)
        minSize_SB.setMinimum(1)
        minSize_SB.setMaximum(2147483647)
        minSize_SB.setValue(5)
        entriesLayout.addWidget(minSize_SB, row, 0, 1, 2)
        self.minSize_SB = minSize_SB

        # Parameters link label
        row += 1
        url = 'https://colab.research.google.com/github/MouseLand/cellpose/blob/master/notebooks/Cellpose_2D_v0_1.ipynb#scrollTo=Rr0UozRm42CA'
        htmlTxt = f'<a href=\"{url}">here</a>'
        seeHereLabel = QLabel()
        seeHereLabel.setText(f'See {htmlTxt} for details on the parameters')
        seeHereLabel.setTextFormat(Qt.RichText)
        seeHereLabel.setTextInteractionFlags(Qt.TextBrowserInteraction)
        seeHereLabel.setOpenExternalLinks(True)
        font = QtGui.QFont()
        font.setPointSize(10)
        seeHereLabel.setFont(font)
        seeHereLabel.setStyleSheet("padding:10px 0px 0px 0px;")
        entriesLayout.addWidget(seeHereLabel, row, 0, 1, 2)

        HBoxLayout = QHBoxLayout()
        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        HBoxLayout.addWidget(okButton, alignment=Qt.AlignRight)

        cancelButton = QPushButton('Cancel')
        # cancelButton.setShortcut(Qt.Key_Escape)
        HBoxLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        HBoxLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(entriesLayout)
        mainLayout.addLayout(HBoxLayout)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setLayout(mainLayout)
        self.setModal(True)

    def updateFlowThreshVal(self, valInt):
        val = valInt/10
        self.flowThreshLabelValue.setText(str(val))

    def updateCellProbVal(self, valInt):
        self.cellProbThreshLabelValue.setText(str(valInt))

    def ok_cb(self, event):
        self.cancel = False
        self.diameter = self.diameterEntry.value()
        self.flow_threshold = self.flowThreshSlider.value()/10
        self.cellprob_threshold = self.cellProbThreshSlider.value()
        self.minSize = self.minSize_SB.value()
        self.close()

    def cancel_cb(self, event):
        self.cancel = True
        self.close()

class YeaZ_ParamsDialog(QDialog):
    def __init__(self, parent=None):
        self.cancel = True
        super().__init__(parent)
        self.setWindowTitle("YeaZ parameters")
        if parent is None:
            self.setWindowIcon(QIcon(":assign-motherbud.svg"))

        mainLayout = QVBoxLayout()

        formLayout = QFormLayout()
        formLayout.setLabelAlignment(Qt.AlignRight)
        formLayout.addRow("Threshold value:", QLineEdit())

        minDist_SB = QSpinBox()
        minDist_SB.setAlignment(Qt.AlignCenter)
        minDist_SB.setMinimum(1)
        minDist_SB.setMaximum(2147483647)
        minDist_SB.setValue(10)
        formLayout.addRow("Minimum distance:", minDist_SB)
        self.minDist_SB = minDist_SB

        minSize_SB = QSpinBox()
        minSize_SB.setAlignment(Qt.AlignCenter)
        minSize_SB.setMinimum(1)
        minSize_SB.setMaximum(2147483647)
        minSize_SB.setValue(5)
        formLayout.addRow(
            "Remove objects smaller than (area in pixels):", minSize_SB
        )
        self.minSize_SB = minSize_SB

        threshVal_QLineEdit = formLayout.itemAt(0, 1).widget()
        threshVal_QLineEdit.setText('None')
        threshVal_QLineEdit.setAlignment(Qt.AlignCenter)
        self.threshVal_QLineEdit = threshVal_QLineEdit


        HBoxLayout = QHBoxLayout()
        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        HBoxLayout.addWidget(okButton, alignment=Qt.AlignRight)

        cancelButton = QPushButton('Cancel')
        # cancelButton.setShortcut(Qt.Key_Escape)
        HBoxLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        HBoxLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(formLayout)
        mainLayout.addLayout(HBoxLayout)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setLayout(mainLayout)
        self.setModal(True)

    def ok_cb(self, event):
        self.cancel = False
        valid_threshVal = False
        valid_minDist = False
        threshTxt = self.threshVal_QLineEdit.text()
        self.minDist = self.minDist_SB.value()
        self.minSize = self.minSize_SB.value()
        try:
            self.threshVal = float(threshTxt)
            if self.threshVal > 0 and self.threshVal < 1:
                valid_threshVal = True
            else:
                valid_threshVal = False
        except Exception as e:
            if threshTxt == 'None':
                self.threshVal = None
                valid_threshVal = True
            else:
                valid_threshVal = False
        if not valid_threshVal:
            err_msg = (
                'Threshold value is not valid. '
                'Enter a floating point from 0 to 1 or "None"'
            )
            msg = QtGui.QMessageBox()
            msg.critical(
                self, 'Invalid threshold value', err_msg, msg.Ok
            )
            return
        self.close()

    def cancel_cb(self, event):
        self.cancel = True
        self.close()

class editCcaTableWidget(QDialog):
    def __init__(self, cca_df, parent=None):
        self.inputCca_df = cca_df
        self.cancel = True
        self.cca_df = None

        super().__init__(parent)
        self.setWindowTitle("Edit cell cycle annotations")

        # Layouts
        mainLayout = QVBoxLayout()
        tableLayout = QGridLayout()
        buttonsLayout = QHBoxLayout()
        self.scrollArea = QScrollArea()
        self.viewBox = QWidget()

        # Header labels
        col = 0
        row = 0
        IDsLabel = QLabel('Cell ID')
        AC = Qt.AlignCenter
        IDsLabel.setAlignment(AC)
        tableLayout.addWidget(IDsLabel, 0, col, alignment=AC)

        col += 1
        ccsLabel = QLabel('Cell cycle stage')
        ccsLabel.setAlignment(Qt.AlignCenter)
        tableLayout.addWidget(ccsLabel, 0, col, alignment=AC)

        col += 1
        genNumLabel = QLabel('Generation number')
        genNumLabel.setAlignment(Qt.AlignCenter)
        tableLayout.addWidget(genNumLabel, 0, col, alignment=AC)
        genNumColWidth = genNumLabel.sizeHint().width()

        col += 1
        relIDLabel = QLabel('Relative ID')
        relIDLabel.setAlignment(Qt.AlignCenter)
        tableLayout.addWidget(relIDLabel, 0, col, alignment=AC)

        col += 1
        relationshipLabel = QLabel('Relationship')
        relationshipLabel.setAlignment(Qt.AlignCenter)
        tableLayout.addWidget(relationshipLabel, 0, col, alignment=AC)

        col += 1
        emergFrameLabel = QLabel('Emerging frame num.')
        emergFrameLabel.setAlignment(Qt.AlignCenter)
        tableLayout.addWidget(emergFrameLabel, 0, col, alignment=AC)

        col += 1
        divitionFrameLabel = QLabel('Division frame num.')
        divitionFrameLabel.setAlignment(Qt.AlignCenter)
        tableLayout.addWidget(divitionFrameLabel, 0, col, alignment=AC)

        col += 1
        historyKnownLabel = QLabel('Is history known?')
        historyKnownLabel.setAlignment(Qt.AlignCenter)
        tableLayout.addWidget(historyKnownLabel, 0, col, alignment=AC)

        tableLayout.setHorizontalSpacing(20)
        self.tableLayout = tableLayout

        # Add buttons
        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)

        cancelButton = QPushButton('Cancel')
        buttonsLayout.addWidget(okButton)
        buttonsLayout.addWidget(cancelButton)

        # Scroll area properties
        self.scrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scrollArea.setFrameStyle(QFrame.NoFrame)
        self.scrollArea.setWidgetResizable(True)

        # Add layouts
        self.viewBox.setLayout(tableLayout)
        self.scrollArea.setWidget(self.viewBox)
        mainLayout.addWidget(self.scrollArea)
        mainLayout.addLayout(buttonsLayout)

        # Populate table Layout
        IDs = cca_df.index
        self.IDs = IDs.to_list()
        relIDsOptions = [str(ID) for ID in IDs]
        relIDsOptions.insert(0, '-1')
        self.IDlabels = []
        self.ccsComboBoxes = []
        self.genNumSpinBoxes = []
        self.relIDComboBoxes = []
        self.relationshipComboBoxes = []
        self.emergFrameSpinBoxes = []
        self.divisFrameSpinBoxes = []
        self.emergFrameSpinPrevValues = []
        self.divisFrameSpinPrevValues = []
        self.historyKnownCheckBoxes = []
        for row, ID in enumerate(IDs):
            col = 0
            IDlabel = QLabel(f'{ID}')
            IDlabel.setAlignment(Qt.AlignCenter)
            tableLayout.addWidget(IDlabel, row+1, col, alignment=AC)
            self.IDlabels.append(IDlabel)

            col += 1
            ccsComboBox = QComboBox()
            ccsComboBox.setFocusPolicy(Qt.StrongFocus)
            ccsComboBox.installEventFilter(self)
            ccsComboBox.addItems(['G1', 'S/G2/M'])
            ccsValue = cca_df.at[ID, 'cell_cycle_stage']
            if ccsValue == 'S':
                ccsValue = 'S/G2/M'
            ccsComboBox.setCurrentText(ccsValue)
            tableLayout.addWidget(ccsComboBox, row+1, col, alignment=AC)
            self.ccsComboBoxes.append(ccsComboBox)
            ccsComboBox.activated.connect(self.clearComboboxFocus)

            col += 1
            genNumSpinBox = QSpinBox()
            genNumSpinBox.setFocusPolicy(Qt.StrongFocus)
            genNumSpinBox.installEventFilter(self)
            genNumSpinBox.setValue(2)
            genNumSpinBox.setAlignment(Qt.AlignCenter)
            genNumSpinBox.setFixedWidth(int(genNumColWidth*2/3))
            genNumSpinBox.setValue(cca_df.at[ID, 'generation_num'])
            tableLayout.addWidget(genNumSpinBox, row+1, col, alignment=AC)
            self.genNumSpinBoxes.append(genNumSpinBox)

            col += 1
            relIDComboBox = QComboBox()
            relIDComboBox.setFocusPolicy(Qt.StrongFocus)
            relIDComboBox.installEventFilter(self)
            relIDComboBox.addItems(relIDsOptions)
            relIDComboBox.setCurrentText(str(cca_df.at[ID, 'relative_ID']))
            tableLayout.addWidget(relIDComboBox, row+1, col)
            self.relIDComboBoxes.append(relIDComboBox)
            relIDComboBox.currentIndexChanged.connect(self.setRelID)
            relIDComboBox.activated.connect(self.clearComboboxFocus)


            col += 1
            relationshipComboBox = QComboBox()
            relationshipComboBox.setFocusPolicy(Qt.StrongFocus)
            relationshipComboBox.installEventFilter(self)
            relationshipComboBox.addItems(['mother', 'bud'])
            relationshipComboBox.setCurrentText(cca_df.at[ID, 'relationship'])
            tableLayout.addWidget(relationshipComboBox, row+1, col)
            self.relationshipComboBoxes.append(relationshipComboBox)
            relationshipComboBox.currentIndexChanged.connect(
                                                self.relationshipChanged_cb)
            relationshipComboBox.activated.connect(self.clearComboboxFocus)

            col += 1
            emergFrameSpinBox = QSpinBox()
            emergFrameSpinBox.setFocusPolicy(Qt.StrongFocus)
            emergFrameSpinBox.installEventFilter(self)
            emergFrameSpinBox.setMinimum(-1)
            emergFrameSpinBox.setValue(-1)
            emergFrameSpinBox.setAlignment(Qt.AlignCenter)
            emergFrameSpinBox.setFixedWidth(int(genNumColWidth*2/3))
            emergFrame_i = cca_df.at[ID, 'emerg_frame_i']
            val = emergFrame_i+1 if emergFrame_i>=0 else -1
            emergFrameSpinBox.setValue(val)
            tableLayout.addWidget(emergFrameSpinBox, row+1, col, alignment=AC)
            self.emergFrameSpinBoxes.append(emergFrameSpinBox)
            self.emergFrameSpinPrevValues.append(emergFrameSpinBox.value())
            emergFrameSpinBox.valueChanged.connect(self.skip0emergFrame)


            col += 1
            divisFrameSpinBox = QSpinBox()
            divisFrameSpinBox.setFocusPolicy(Qt.StrongFocus)
            divisFrameSpinBox.installEventFilter(self)
            divisFrameSpinBox.setMinimum(-1)
            divisFrameSpinBox.setValue(-1)
            divisFrameSpinBox.setAlignment(Qt.AlignCenter)
            divisFrameSpinBox.setFixedWidth(int(genNumColWidth*2/3))
            divisFrame_i = cca_df.at[ID, 'division_frame_i']
            val = divisFrame_i+1 if divisFrame_i>=0 else -1
            divisFrameSpinBox.setValue(val)
            tableLayout.addWidget(divisFrameSpinBox, row+1, col, alignment=AC)
            self.divisFrameSpinBoxes.append(divisFrameSpinBox)
            self.divisFrameSpinPrevValues.append(divisFrameSpinBox.value())
            emergFrameSpinBox.valueChanged.connect(self.skip0divisFrame)

            col += 1
            HistoryCheckBox = QCheckBox()
            HistoryCheckBox.setChecked(bool(cca_df.at[ID, 'is_history_known']))
            tableLayout.addWidget(HistoryCheckBox, row+1, col, alignment=AC)
            self.historyKnownCheckBoxes.append(HistoryCheckBox)

        # Contents margins
        buttonsLayout.setContentsMargins(200, 15, 200, 15)

        self.setLayout(mainLayout)

        # Connect to events
        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setModal(True)

    def setRelID(self, itemIndex):
        idx = self.relIDComboBoxes.index(self.sender())
        relID = self.sender().currentText()
        IDofRelID = self.IDs[idx]
        relIDidx = self.IDs.index(int(relID))
        relIDComboBox = self.relIDComboBoxes[relIDidx]
        relIDComboBox.setCurrentText(str(IDofRelID))

    def skip0emergFrame(self, value):
        idx = self.emergFrameSpinBoxes.index(self.sender())
        prevVal = self.emergFrameSpinPrevValues[idx]
        if value == 0 and value > prevVal:
            self.sender().setValue(1)
            self.emergFrameSpinPrevValues[idx] = 1
        elif value == 0 and value < prevVal:
            self.sender().setValue(-1)
            self.emergFrameSpinPrevValues[idx] = -1

    def skip0divisFrame(self, value):
        idx = self.divisFrameSpinBoxes.index(self.sender())
        prevVal = self.divisFrameSpinPrevValues[idx]
        if value == 0 and value > prevVal:
            self.sender().setValue(1)
            self.divisFrameSpinPrevValues[idx] = 1
        elif value == 0 and value < prevVal:
            self.sender().setValue(-1)
            self.divisFrameSpinPrevValues[idx] = -1

    def relationshipChanged_cb(self, itemIndex):
        idx = self.relationshipComboBoxes.index(self.sender())
        ccs = self.sender().currentText()
        if ccs == 'bud':
            self.ccsComboBoxes[idx].setCurrentText('S/G2/M')
            self.genNumSpinBoxes[idx].setValue(0)

    def getCca_df(self):
        ccsValues = [var.currentText() for var in self.ccsComboBoxes]
        ccsValues = [val if val=='G1' else 'S' for val in ccsValues]
        genNumValues = [var.value() for var in self.genNumSpinBoxes]
        relIDValues = [int(var.currentText()) for var in self.relIDComboBoxes]
        relatValues = [var.currentText() for var in self.relationshipComboBoxes]
        emergFrameValues = [var.value()-1 if var.value()>0 else -1
                            for var in self.emergFrameSpinBoxes]
        divisFrameValues = [var.value()-1 if var.value()>0 else -1
                            for var in self.divisFrameSpinBoxes]
        historyValues = [var.isChecked() for var in self.historyKnownCheckBoxes]
        check_rel = [ID == relID for ID, relID in zip(self.IDs, relIDValues)]
        # Buds in S phase must have 0 as number of cycles
        check_buds_S = [ccs=='S' and rel_ship=='bud' and not numc==0
                        for ccs, rel_ship, numc
                        in zip(ccsValues, relatValues, genNumValues)]
        # Mother cells must have at least 1 as number of cycles if history known
        check_mothers = [rel_ship=='mother' and not numc>=1
                         if is_history_known else False
                         for rel_ship, numc, is_history_known
                         in zip(relatValues, genNumValues, historyValues)]
        # Buds cannot be in G1
        check_buds_G1 = [ccs=='G1' and rel_ship=='bud'
                         for ccs, rel_ship
                         in zip(ccsValues, relatValues)]
        # The number of cells in S phase must be half mothers and half buds
        num_moth_S = len([0 for ccs, rel_ship in zip(ccsValues, relatValues)
                            if ccs=='S' and rel_ship=='mother'])
        num_bud_S = len([0 for ccs, rel_ship in zip(ccsValues, relatValues)
                            if ccs=='S' and rel_ship=='bud'])
        # Cells in S phase cannot have -1 as relative's ID
        check_relID_S = [ccs=='S' and relID==-1
                         for ccs, relID
                         in zip(ccsValues, relIDValues)]
        if any(check_rel):
            QtGui.QMessageBox().critical(self,
                    'Cell ID = Relative\'s ID', 'Some cells are '
                    'mother or bud of itself. Make sure that the Relative\'s ID'
                    ' is different from the Cell ID!',
                    QtGui.QMessageBox.Ok)
            return None
        elif any(check_buds_S):
            QtGui.QMessageBox().critical(self,
                'Bud in S/G2/M not in 0 Generation number',
                'Some buds '
                'in S phase do not have 0 as Generation number!\n'
                'Buds in S phase must have 0 as "Generation number"',
                QtGui.QMessageBox.Ok)
            return None
        elif any(check_mothers):
            QtGui.QMessageBox().critical(self,
                'Mother not in >=1 Generation number',
                'Some mother cells do not have >=1 as "Generation number"!\n'
                'Mothers MUST have >1 "Generation number"',
                QtGui.QMessageBox.Ok)
            return None
        elif any(check_buds_G1):
            QtGui.QMessageBox().critical(self,
                'Buds in G1!',
                'Some buds are in G1 phase!\n'
                'Buds MUST be in S/G2/M phase',
                QtGui.QMessageBox.Ok)
            return None
        elif num_moth_S != num_bud_S:
            QtGui.QMessageBox().critical(self,
                'Number of mothers-buds mismatch!',
                f'There are {num_moth_S} mother cells in "S/G2/M" phase,'
                f'but there are {num_bud_S} bud cells.\n\n'
                'The number of mothers and buds in "S/G2/M" '
                'phase must be equal!',
                QtGui.QMessageBox.Ok)
            return None
        elif any(check_relID_S):
            QtGui.QMessageBox().critical(self,
                'Relative\'s ID of cells in S/G2/M = -1',
                'Some cells are in "S/G2/M" phase but have -1 as Relative\'s ID!\n'
                'Cells in "S/G2/M" phase must have an existing '
                'ID as Relative\'s ID!',
                QtGui.QMessageBox.Ok)
            return None
        else:
            corrected_assignment = self.inputCca_df['corrected_assignment']
            cca_df = pd.DataFrame({
                                'cell_cycle_stage': ccsValues,
                                'generation_num': genNumValues,
                                'relative_ID': relIDValues,
                                'relationship': relatValues,
                                'emerg_frame_i': emergFrameValues,
                                'division_frame_i': divisFrameValues,
                                'is_history_known': historyValues,
                                'corrected_assignment': corrected_assignment},
                                index=self.IDs)
            cca_df.index.name = 'Cell_ID'
            d = dict.fromkeys(cca_df.select_dtypes(np.int64).columns, np.int32)
            cca_df = cca_df.astype(d)
            return cca_df

    def ok_cb(self, checked):
        cca_df = self.getCca_df()
        if cca_df is None:
            return
        self.cca_df = cca_df
        self.cancel = False
        self.close()

    def cancel_cb(self, checked):
        self.cancel = True
        self.close()

    def showAndSetWidth(self):
        self.show()
        w = self.viewBox.minimumSizeHint().width() + 5*self.tableLayout.columnCount()
        winGeometry = self.geometry()
        l, t, h = winGeometry.left(), winGeometry.top(), winGeometry.height()
        self.setGeometry(l, t, w, h)

    def eventFilter(self, object, event):
        # Disable wheel scroll on widgets to allow scroll only on scrollarea
        if event.type() == QEvent.Wheel:
            event.ignore()
            return True
        return False

    def clearComboboxFocus(self):
        self.sender().clearFocus()

class askStopFrameSegm(QDialog):
    def __init__(self, user_ch_file_paths, user_ch_name, parent=None):
        self.parent = parent
        self.cancel = True

        super().__init__(parent)
        self.setWindowTitle('Enter stop frame')

        mainLayout = QVBoxLayout()
        formLayout = QFormLayout()
        buttonsLayout = QHBoxLayout()

        # Message
        infoTxt = (
        """
        <!DOCTYPE html>
        <html>
        <head>
        <style>
        p.big {
            line-height: 1.2;
        }
        </style>
        </head>
        <body>
        <p class="big">
            Enter a <b>stop frame number</b> when to stop<br>
            segmentation for each Position loaded:
        </p>
        </body>
        </html>
        """
        )
        infoLabel = QLabel(infoTxt, self)
        _font = QtGui.QFont()
        _font.setPointSize(10)
        infoLabel.setFont(_font)
        infoLabel.setAlignment(Qt.AlignCenter)
        # padding: top, left, bottom, right
        infoLabel.setStyleSheet("padding:0px 0px 8px 0px;")

        self.dataDict = {}

        # Form layout widget
        for img_path in user_ch_file_paths:
            pos_foldername = os.path.basename(
                os.path.dirname(
                    os.path.dirname(img_path)
                )
            )
            spinBox = QSpinBox()
            PosData = load.loadData(img_path, user_ch_name, QParent=parent)
            PosData.getBasenameAndChNames(prompts.select_channel_name)
            PosData.buildPaths()
            PosData.loadImgData()
            PosData.loadOtherFiles(
                load_segm_data=False,
                load_metadata=True,
                loadSegmInfo=True,
                )
            spinBox.setMaximum(PosData.SizeT)
            if PosData.segmSizeT == 1:
                spinBox.setValue(PosData.SizeT)
            else:
                spinBox.setValue(PosData.segmSizeT)
            spinBox.setAlignment(Qt.AlignCenter)
            visualizeButton = QPushButton('Visualize')
            visualizeButton.clicked.connect(self.visualize_cb)
            formLabel = QLabel(f'{pos_foldername}  ')
            layout = QHBoxLayout()
            layout.addWidget(formLabel, alignment=Qt.AlignRight)
            layout.addWidget(spinBox)
            layout.addWidget(visualizeButton)
            self.dataDict[visualizeButton] = (spinBox, PosData)
            formLayout.addRow(layout)

        self.formLayout = formLayout
        mainLayout.addWidget(infoLabel, alignment=Qt.AlignCenter)
        mainLayout.addLayout(formLayout)

        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)

        cancelButton = QPushButton('Cancel')

        buttonsLayout.addWidget(okButton, alignment=Qt.AlignRight)
        buttonsLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.close)

        mainLayout.addLayout(buttonsLayout)

        self.setLayout(mainLayout)

        # self.setModal(True)

    def saveSegmSizeT(self):
        for spinBox, PosData in self.dataDict.values():
            PosData.segmSizeT = spinBox.value()
            PosData.saveMetadata()

    def ok_cb(self, event):
        self.cancel = False
        self.saveSegmSizeT()
        self.close()

    def visualize_cb(self, checked=True):
        spinBox, PosData = self.dataDict[self.sender()]
        PosData.frame_i = spinBox.value()-1
        self.slideshowWin = CellsSlideshow_GUI(PosData=PosData,
                                               spinBox=spinBox)
        self.slideshowWin.update_img()
        self.slideshowWin.show()

    def showAndSetFont(self, font):
        self.show()
        self.setFont(font)

class QLineEditDialog(QDialog):
    def __init__(self, title='Entry messagebox', msg='Entry value',
                       defaultTxt='', parent=None):
        self.cancel = True

        super().__init__(parent)
        self.setWindowTitle(title)

        # Layouts
        mainLayout = QVBoxLayout()
        LineEditLayout = QVBoxLayout()
        buttonsLayout = QHBoxLayout()

        # Widgets
        msg = QLabel(msg)
        _font = QtGui.QFont()
        _font.setPointSize(10)
        msg.setFont(_font)
        msg.setAlignment(Qt.AlignCenter)
        # padding: top, left, bottom, right
        msg.setStyleSheet("padding:0px 0px 3px 0px;")

        ID_QLineEdit = QLineEdit()
        ID_QLineEdit.setFont(_font)
        ID_QLineEdit.setAlignment(Qt.AlignCenter)
        ID_QLineEdit.setText(defaultTxt)
        self.ID_QLineEdit = ID_QLineEdit

        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)

        cancelButton = QPushButton('Cancel')

        # Events
        ID_QLineEdit.textChanged[str].connect(self.ID_LineEdit_cb)
        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        # Contents margins
        buttonsLayout.setContentsMargins(0,10,0,0)

        # Add widgets to layouts
        LineEditLayout.addWidget(msg, alignment=Qt.AlignCenter)
        LineEditLayout.addWidget(ID_QLineEdit, alignment=Qt.AlignCenter)
        buttonsLayout.addWidget(okButton)
        buttonsLayout.addWidget(cancelButton)

        # Add layouts
        mainLayout.addLayout(LineEditLayout)
        mainLayout.addLayout(buttonsLayout)

        self.setLayout(mainLayout)

        self.setModal(True)

    def ID_LineEdit_cb(self, text):
        # Get inserted char
        idx = self.ID_QLineEdit.cursorPosition()
        if idx == 0:
            return

        newChar = text[idx-1]

        # Allow only integers
        try:
            int(newChar)
        except Exception as e:
            text = text.replace(newChar, '')
            self.ID_QLineEdit.setText(text)
            return

    def ok_cb(self, event):
        self.cancel = False
        self.EntryID = int(self.ID_QLineEdit.text())
        self.close()

    def cancel_cb(self, event):
        self.cancel = True
        self.close()


class editID_QWidget(QDialog):
    def __init__(self, clickedID, IDs, parent=None):
        self.IDs = IDs
        self.clickedID = clickedID
        self.cancel = True
        self.how = None

        super().__init__(parent)
        self.setWindowTitle("Edit ID")
        mainLayout = QVBoxLayout()

        VBoxLayout = QVBoxLayout()
        msg = QLabel(f'Replace ID {clickedID} with:')
        _font = QtGui.QFont()
        _font.setPointSize(10)
        msg.setFont(_font)
        # padding: top, left, bottom, right
        msg.setStyleSheet("padding:0px 0px 3px 0px;")
        VBoxLayout.addWidget(msg, alignment=Qt.AlignCenter)

        ID_QLineEdit = QLineEdit()
        ID_QLineEdit.setFont(_font)
        ID_QLineEdit.setAlignment(Qt.AlignCenter)
        self.ID_QLineEdit = ID_QLineEdit
        VBoxLayout.addWidget(ID_QLineEdit)

        note = QLabel(
            'NOTE: To replace multiple IDs at once\n'
            'write "(old ID, new ID), (old ID, new ID)" etc.'
        )
        note.setFont(_font)
        note.setAlignment(Qt.AlignCenter)
        # padding: top, left, bottom, right
        note.setStyleSheet("padding:10px 0px 0px 0px;")
        VBoxLayout.addWidget(note, alignment=Qt.AlignCenter)
        mainLayout.addLayout(VBoxLayout)

        HBoxLayout = QHBoxLayout()
        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        HBoxLayout.addWidget(okButton, alignment=Qt.AlignRight)

        cancelButton = QPushButton('Cancel')
        # cancelButton.setShortcut(Qt.Key_Escape)
        HBoxLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        HBoxLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(HBoxLayout)

        self.setLayout(mainLayout)

        # Connect events
        self.prevText = ''
        ID_QLineEdit.textChanged[str].connect(self.ID_LineEdit_cb)
        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setModal(True)

    def ID_LineEdit_cb(self, text):
        # Get inserted char
        idx = self.ID_QLineEdit.cursorPosition()
        if idx == 0:
            return

        newChar = text[idx-1]

        # Do nothing if user is deleting text
        if idx == 0 or len(text)<len(self.prevText):
            self.prevText = text
            return

        # Do not allow chars except for "(", ")", "int", ","
        m = re.search(r'\(|\)|\d|,', newChar)
        if m is None:
            self.prevText = text
            text = text.replace(newChar, '')
            self.ID_QLineEdit.setText(text)
            return

        # Automatically close ( bracket
        if newChar == '(':
            text += ')'
            self.ID_QLineEdit.setText(text)
        self.prevText = text

    def ok_cb(self, event):
        self.cancel = False
        txt = self.ID_QLineEdit.text()
        valid = False

        # Check validity of inserted text
        try:
            ID = int(txt)
            how = [(self.clickedID, ID)]
            if ID in self.IDs:
                warn_msg = (
                    f'ID {ID} is already existing. If you continue ID {ID} '
                    f'will be swapped with ID {self.clickedID}\n\n'
                    'Do you want to continue?'
                )
                msg = QtGui.QMessageBox()
                do_swap = msg.warning(
                    self, 'Invalid entry', warn_msg, msg.Yes | msg.Cancel
                )
                if do_swap == msg.Yes:
                    valid = True
                else:
                    return
            else:
                valid = True
        except ValueError:
            pattern = '\((\d+),\s*(\d+)\)'
            fa = re.findall(pattern, txt)
            if fa:
                how = [(int(g[0]), int(g[1])) for g in fa]
                valid = True
            else:
                valid = False

        if valid:
            self.how = how
            self.close()
        else:
            err_msg = (
                'You entered invalid text. Valid text is either a single integer'
                f' ID that will be used to replace ID {self.clickedID} '
                'or a list of elements enclosed in parenthesis separated by a comma\n'
                'such as (5, 10), (8, 27) to replace ID 5 with ID 10 and ID 8 with ID 27'
            )
            msg = QtGui.QMessageBox()
            msg.critical(
                self, 'Invalid entry', err_msg, msg.Ok
            )

    def cancel_cb(self, event):
        self.cancel = True
        self.close()

def YeaZ_Params():
    app = QApplication(sys.argv)
    params = YeaZ_ParamsDialog()
    params.show()
    app.setStyle(QtGui.QStyleFactory.create('Fusion'))
    app.exec_()
    return params


class tk_breakpoint:
    '''Geometry: "WidthxHeight+Left+Top" '''
    def __init__(self, title='Breakpoint', geometry="+800+400",
                 message='Breakpoint', button_1_text='Continue',
                 button_2_text='Abort', button_3_text='Delete breakpoint'):
        self.abort = False
        self.next_i = False
        self.del_breakpoint = False
        self.title = title
        self.geometry = geometry
        self.message = message
        self.button_1_text = button_1_text
        self.button_2_text = button_2_text
        self.button_3_text = button_3_text

    def pausehere(self):
        global root
        if not self.del_breakpoint:
            root = tk.Tk()
            root.lift()
            root.attributes("-topmost", True)
            root.title(self.title)
            root.geometry(self.geometry)
            tk.Label(root,
                     text=self.message,
                     font=(None, 11)).grid(row=0, column=0,
                                           columnspan=2, pady=4, padx=4)

            tk.Button(root,
                      text=self.button_1_text,
                      command=self.continue_button,
                      width=10,).grid(row=4,
                                      column=0,
                                      pady=8, padx=8)

            tk.Button(root,
                      text=self.button_2_text,
                      command=self.abort_button,
                      width=15).grid(row=4,
                                     column=1,
                                     pady=8, padx=8)
            tk.Button(root,
                      text=self.button_3_text,
                      command=self.delete_breakpoint,
                      width=20).grid(row=5,
                                     column=0,
                                     columnspan=2,
                                     pady=(0,8))

            root.mainloop()

    def continue_button(self):
        self.next_i=True
        root.quit()
        root.destroy()

    def delete_breakpoint(self):
        self.del_breakpoint=True
        root.quit()
        root.destroy()

    def abort_button(self):
        self.abort=True
        exit('Execution aborted by the user')
        root.quit()
        root.destroy()

class imshow_tk:
    def __init__(self, img, dots_coords=None, x_idx=1, axis=None,
                       additional_imgs=[], titles=[], fixed_vrange=False,
                       run=True):
        if img.ndim == 3:
            if img.shape[-1] > 4:
                img = img.max(axis=0)
                h, w = img.shape
            else:
                h, w, _ = img.shape
        elif img.ndim == 2:
            h, w = img.shape
        elif img.ndim != 2 and img.ndim != 3:
            raise TypeError(f'Invalid shape {img.shape} for image data. '
            'Only 2D or 3D images.')
        for i, im in enumerate(additional_imgs):
            if im.ndim == 3 and im.shape[-1] > 4:
                additional_imgs[i] = im.max(axis=0)
            elif im.ndim != 2 and im.ndim != 3:
                raise TypeError(f'Invalid shape {im.shape} for image data. '
                'Only 2D or 3D images.')
        n_imgs = len(additional_imgs)+1
        if w/h > 1:
            fig, ax = plt.subplots(n_imgs, 1, sharex=True, sharey=True)
        else:
            fig, ax = plt.subplots(1, n_imgs, sharex=True, sharey=True)
        if n_imgs == 1:
            ax = [ax]
        self.ax0img = ax[0].imshow(img)
        if dots_coords is not None:
            ax[0].plot(dots_coords[:,x_idx], dots_coords[:,x_idx-1], 'r.')
        if axis:
            ax[0].axis('off')
        if fixed_vrange:
            vmin, vmax = img.min(), img.max()
        else:
            vmin, vmax = None, None
        self.additional_aximgs = []
        for i, img_i in enumerate(additional_imgs):
            axi_img = ax[i+1].imshow(img_i, vmin=vmin, vmax=vmax)
            self.additional_aximgs.append(axi_img)
            if dots_coords is not None:
                ax[i+1].plot(dots_coords[:,x_idx], dots_coords[:,x_idx-1], 'r.')
            if axis:
                ax[i+1].axis('off')
        for title, a in zip(titles, ax):
            a.set_title(title)
        sub_win = embed_tk('Imshow embedded in tk', [800,600,400,150], fig)
        sub_win.root.protocol("WM_DELETE_WINDOW", self._close)
        self.sub_win = sub_win
        self.fig = fig
        self.ax = ax
        sub_win.root.wm_attributes('-topmost',True)
        sub_win.root.focus_force()
        sub_win.root.after_idle(sub_win.root.attributes,'-topmost',False)
        if run:
            sub_win.root.mainloop()

    def _close(self):
        plt.close(self.fig)
        self.sub_win.root.quit()
        self.sub_win.root.destroy()

class embed_tk:
    """Example:
    -----------
    img = np.ones((600,600))
    fig = plt.Figure(figsize=(5, 4), dpi=100)
    ax = fig.add_subplot()
    ax.imshow(img)

    sub_win = embed_tk('Embeddding in tk', [1024,768,300,100], fig)

    def on_key_event(event):
        print('you pressed %s' % event.key)

    sub_win.canvas.mpl_connect('key_press_event', on_key_event)

    sub_win.root.mainloop()
    """
    def __init__(self, win_title, geom, fig):
        root = tk.Tk()
        root.wm_title(win_title)
        root.geometry("{}x{}+{}+{}".format(*geom)) # WidthxHeight+Left+Top
        # a tk.DrawingArea
        canvas = FigureCanvasTkAgg(fig, master=root)
        canvas.draw()
        canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        toolbar = NavigationToolbar2Tk(canvas, root)
        toolbar.update()
        canvas._tkcanvas.pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        self.canvas = canvas
        self.toolbar = toolbar
        self.root = root

class auto_select_slice:
    def __init__(self, auto_focus=True, prompt_use_for_all=False):
        self.auto_focus = auto_focus
        self.prompt_use_for_all = prompt_use_for_all
        self.use_for_all = False

    def run(self, frame_V, segm_slice=0, segm_npy=None, IDs=None):
        if self.auto_focus:
            auto_slice = self.auto_slice(frame_V)
        else:
            auto_slice = 0
        self.segm_slice = segm_slice
        self.slice = auto_slice
        self.abort = True
        self.data = frame_V
        self.fig = plt.Figure()
        self.ax = self.fig.add_subplot()
        self.fig.subplots_adjust(bottom=0.20)
        sl_width = 0.6
        sl_left = 0.5 - (sl_width/2)
        ok_width = 0.13
        ok_left = 0.5 - (ok_width/2)
        (self.ax).imshow(frame_V[auto_slice])
        if segm_npy is not None:
            self.contours = self.find_contours(segm_npy, IDs, group=True)
            for cont in self.contours:
                x = cont[:,1]
                y = cont[:,0]
                x = np.append(x, x[0])
                y = np.append(y, y[0])
                (self.ax).plot(x, y, c='r')
        (self.ax).axis('off')
        (self.ax).set_title('Select slice for amount calculation\n\n'
                    f'Slice used for segmentation: {segm_slice}\n'
                    f'Best focus determined by algorithm: slice {auto_slice}')
        """Embed plt window into a tkinter window"""
        sub_win = embed_tk('Mother-bud zoom', [1024,768,400,150], self.fig)
        self.ax_sl = self.fig.add_subplot(
                                position=[sl_left, 0.12, sl_width, 0.04],
                                facecolor='0.1')
        self.sl = Slider(self.ax_sl, 'Slice', -1, len(frame_V),
                                canvas=sub_win.canvas,
                                valinit=auto_slice,
                                valstep=1,
                                color='0.2',
                                init_val_line_color='0.3',
                                valfmt='%1.0f')
        (self.sl).on_changed(self.update_slice)
        self.ax_ok = self.fig.add_subplot(
                                position=[ok_left, 0.05, ok_width, 0.05],
                                facecolor='0.1')
        self.ok_b = Button(self.ax_ok, 'Happy with that', canvas=sub_win.canvas,
                                color='0.1',
                                hovercolor='0.25',
                                presscolor='0.35')
        (self.ok_b).on_clicked(self.ok)
        (sub_win.root).protocol("WM_DELETE_WINDOW", self.abort_exec)
        (sub_win.canvas).mpl_connect('key_press_event', self.set_slvalue)
        self.sub_win = sub_win
        sub_win.root.wm_attributes('-topmost',True)
        sub_win.root.focus_force()
        sub_win.root.after_idle(sub_win.root.attributes,'-topmost',False)
        sub_win.root.mainloop()

    def find_contours(self, label_img, cells_ids, group=False, concat=False,
                      return_hull=False):
        contours = []
        for id in cells_ids:
            label_only_cells_ids_img = np.zeros_like(label_img)
            label_only_cells_ids_img[label_img == id] = id
            uint8_img = (label_only_cells_ids_img > 0).astype(np.uint8)
            cont, hierarchy = cv2.findContours(uint8_img,cv2.RETR_LIST,
                                               cv2.CHAIN_APPROX_NONE)
            cnt = cont[0]
            if return_hull:
                hull = cv2.convexHull(cnt,returnPoints = True)
                contours.append(hull)
            else:
                contours.append(cnt)
        if concat:
            all_contours = np.zeros((0,2), dtype=int)
            for contour in contours:
                contours_2D_yx = np.fliplr(np.reshape(contour, (contour.shape[0],2)))
                all_contours = np.concatenate((all_contours, contours_2D_yx))
        elif group:
            # Return a list of n arrays for n objects. Each array has i rows of
            # [y,x] coords for each ith pixel in the nth object's contour
            all_contours = [[] for _ in range(len(cells_ids))]
            for c in contours:
                c2Dyx = np.fliplr(np.reshape(c, (c.shape[0],2)))
                for y,x in c2Dyx:
                    ID = label_img[y, x]
                    idx = list(cells_ids).index(ID)
                    all_contours[idx].append([y,x])
            all_contours = [np.asarray(li) for li in all_contours]
            IDs = [label_img[c[0,0],c[0,1]] for c in all_contours]
        else:
            all_contours = [np.fliplr(np.reshape(contour,
                            (contour.shape[0],2))) for contour in contours]
        return all_contours

    def auto_slice(self, frame_V):
        # https://stackoverflow.com/questions/6646371/detect-which-image-is-sharper
        means = []
        for i, img in enumerate(frame_V):
            edge = sobel(img)
            means.append(np.mean(edge))
        slice = means.index(max(means))
        print('Best slice = {}'.format(slice))
        return slice

    def set_slvalue(self, event):
        if event.key == 'left':
            self.sl.set_val(self.sl.val - 1)
        if event.key == 'right':
            self.sl.set_val(self.sl.val + 1)
        if event.key == 'enter':
            self.ok(None)

    def update_slice(self, val):
        self.slice = int(val)
        img = self.data[int(val)]
        self.ax.imshow(img)
        self.fig.canvas.draw_idle()

    def ok(self, event):
        use_for_all = False
        if self.prompt_use_for_all:
            use_for_all = tk.messagebox.askyesno('Use same slice for all',
                          f'Do you want to use slice {self.slice} for all positions?')
        if use_for_all:
            self.use_for_all = use_for_all
        plt.close(self.fig)
        self.sub_win.root.quit()
        self.sub_win.root.destroy()

    def abort_exec(self):
        plt.close(self.fig)
        self.sub_win.root.quit()
        self.sub_win.root.destroy()
        exit('Execution aborted by the user')

class win_size:
    def __init__(self, w=1, h=1, swap_screen=False):
        try:
            monitor = Display()
            screens = monitor.get_screens()
            num_screens = len(screens)
            displ_w = int(screens[0].width*w)
            displ_h = int(screens[0].height*h)
            x_displ = screens[0].x
            #Display plots maximized window
            mng = plt.get_current_fig_manager()
            if swap_screen:
                geom = "{}x{}+{}+{}".format(displ_w,(displ_h-70),(displ_w-8), 0)
                mng.window.wm_geometry(geom) #move GUI window to second monitor
                                             #with string "widthxheight+x+y"
            else:
                geom = "{}x{}+{}+{}".format(displ_w,(displ_h-70),-8, 0)
                mng.window.wm_geometry(geom) #move GUI window to second monitor
                                             #with string "widthxheight+x+y"
        except Exception as e:
            try:
                mng = plt.get_current_fig_manager()
                mng.window.state('zoomed')
            except Exception as e:
                pass

class QtSelectItems(QDialog):
    def __init__(self, title, items, informativeText,
                 CbLabel='Select value:  ', parent=None):
        self.cancel = True
        self.selectedItemsText = ''
        self.selectedItemsIdx = None
        self.items = items
        super().__init__(parent)
        self.setWindowTitle(title)

        mainLayout = QVBoxLayout()
        topLayout = QHBoxLayout()
        self.topLayout = topLayout
        bottomLayout = QHBoxLayout()

        if informativeText:
            infoLabel = QLabel(informativeText)
            mainLayout.addWidget(infoLabel, alignment=Qt.AlignCenter)

        label = QLabel(CbLabel)
        topLayout.addWidget(label)

        combobox = QComboBox(self)
        combobox.addItems(items)
        self.ComboBox = combobox
        topLayout.addWidget(combobox)
        topLayout.setContentsMargins(0, 10, 0, 0)

        okButton = QPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        bottomLayout.addWidget(okButton, alignment=Qt.AlignRight)

        cancelButton = QPushButton('Cancel')
        bottomLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)

        multiPosButton = QPushButton('Multiple selection')
        multiPosButton.setCheckable(True)
        self.multiPosButton = multiPosButton
        bottomLayout.addWidget(multiPosButton, alignment=Qt.AlignLeft)

        listBox = QListWidget()
        listBox.addItems(items)
        listBox.setSelectionMode(QAbstractItemView.ExtendedSelection)
        listBox.setCurrentRow(0)
        topLayout.addWidget(listBox)
        listBox.hide()
        self.ListBox = listBox

        bottomLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(topLayout)
        mainLayout.addLayout(bottomLayout)
        self.setLayout(mainLayout)

        self.setModal(True)

        # Connect events
        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.close)
        multiPosButton.toggled.connect(self.toggleMultiSelection)

    def toggleMultiSelection(self, checked):
        if checked:
            self.multiPosButton.setText('Single selection')
            self.ComboBox.hide()
            self.ListBox.show()
            # Show 10 items
            n = self.ListBox.count()
            if n > 10:
                h = sum([self.ListBox.sizeHintForRow(i) for i in range(10)])
            else:
                h = sum([self.ListBox.sizeHintForRow(i) for i in range(n)])
            self.ListBox.setFixedHeight(h+5)
        else:
            self.multiPosButton.setText('Multiple selection')
            self.ListBox.hide()
            self.ComboBox.show()
            self.ComboBox.setCurrentIndex(0)
            self.ComboBox.setFocusPolicy(Qt.StrongFocus)
            self.ComboBox.setFocus(True)


    def ok_cb(self, event):
        self.cancel = False
        if self.multiPosButton.isChecked():
            selectedItems = self.ListBox.selectedItems()
            selectedItemsText = [item.text() for item in selectedItems]
            self.selectedItemsText = natsorted(selectedItemsText)
            self.selectedItemsIdx = [self.items.index(txt)
                                     for txt in self.selectedItemsText]
        else:
            self.selectedItemsText = [self.ComboBox.currentText()]
            self.selectedItemsIdx = [self.ComboBox.currentIndex()]
        self.close()

class manualSeparateGui(QMainWindow):
    def __init__(self, lab, ID, img, fontSize='12pt',
                 IDcolor=[255, 255, 0], parent=None,
                 loop=None):
        super().__init__(parent)
        self.loop = loop
        self.cancel = True
        self.parent = parent
        self.lab = lab.copy()
        self.lab[lab!=ID] = 0
        self.ID = ID
        self.img = skimage.exposure.equalize_adapthist(img/img.max())
        self.IDcolor = IDcolor
        self.countClicks = 0
        self.prevLabs = []
        self.prevAllCutsCoords = []
        self.labelItemsIDs = []
        self.undoIdx = 0
        self.fontSize = fontSize
        self.AllCutsCoords = []
        self.setWindowTitle("Cell-ACDC - Segm&Track")
        # self.setGeometry(Left, Top, 850, 800)

        self.gui_createActions()
        self.gui_createMenuBar()
        self.gui_createToolBars()

        self.gui_createStatusBar()

        self.gui_createGraphics()
        self.gui_connectImgActions()

        self.gui_createImgWidgets()
        self.gui_connectActions()

        self.updateImg()
        self.zoomToObj()

        mainContainer = QtGui.QWidget()
        self.setCentralWidget(mainContainer)

        mainLayout = QtGui.QGridLayout()
        mainLayout.addWidget(self.graphLayout, 0, 0, 1, 1)
        mainLayout.addLayout(self.img_Widglayout, 1, 0)

        mainContainer.setLayout(mainLayout)

        self.setWindowModality(Qt.WindowModal)

    def centerWindow(self):
        parent = self.parent
        if parent is not None:
            # Center the window on main window
            mainWinGeometry = parent.geometry()
            mainWinLeft = mainWinGeometry.left()
            mainWinTop = mainWinGeometry.top()
            mainWinWidth = mainWinGeometry.width()
            mainWinHeight = mainWinGeometry.height()
            mainWinCenterX = int(mainWinLeft + mainWinWidth/2)
            mainWinCenterY = int(mainWinTop + mainWinHeight/2)
            winGeometry = self.geometry()
            winWidth = winGeometry.width()
            winHeight = winGeometry.height()
            winLeft = int(mainWinCenterX - winWidth/2)
            winRight = int(mainWinCenterY - winHeight/2)
            self.move(winLeft, winRight)

    def gui_createActions(self):
        # File actions
        self.exitAction = QAction("&Exit", self)
        self.helpAction = QAction('Help', self)
        self.undoAction = QAction(QIcon(":undo.svg"), "Undo (Ctrl+Z)", self)
        self.undoAction.setEnabled(False)
        self.undoAction.setShortcut("Ctrl+Z")

        self.okAction = QAction(QIcon(":applyCrop.svg"), "Happy with that", self)
        self.cancelAction = QAction(QIcon(":cancel.svg"), "Cancel", self)

    def gui_createMenuBar(self):
        menuBar = self.menuBar()
        style = "QMenuBar::item:selected { background: white; }"
        menuBar.setStyleSheet(style)
        # File menu
        fileMenu = QMenu("&File", self)
        menuBar.addMenu(fileMenu)

        menuBar.addAction(self.helpAction)
        fileMenu.addAction(self.exitAction)

    def gui_createToolBars(self):
        toolbarSize = 30

        editToolBar = QToolBar("Edit", self)
        editToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        self.addToolBar(editToolBar)

        editToolBar.addAction(self.okAction)
        editToolBar.addAction(self.cancelAction)

        editToolBar.addAction(self.undoAction)

        self.overlayButton = QToolButton(self)
        self.overlayButton.setIcon(QIcon(":overlay.svg"))
        self.overlayButton.setCheckable(True)
        self.overlayButton.setToolTip('Overlay cells image')
        editToolBar.addWidget(self.overlayButton)

    def gui_connectActions(self):
        self.exitAction.triggered.connect(self.close)
        self.helpAction.triggered.connect(self.help)
        self.okAction.triggered.connect(self.ok_cb)
        self.cancelAction.triggered.connect(self.close)
        self.undoAction.triggered.connect(self.undo)
        self.overlayButton.toggled.connect(self.toggleOverlay)
        self.hist.sigLookupTableChanged.connect(self.histLUT_cb)

    def gui_createStatusBar(self):
        self.statusbar = self.statusBar()
        # Temporary message
        self.statusbar.showMessage("Ready", 3000)
        # Permanent widget
        self.wcLabel = QLabel(f"")
        self.statusbar.addPermanentWidget(self.wcLabel)

    def gui_createGraphics(self):
        self.graphLayout = pg.GraphicsLayoutWidget()

        # Plot Item container for image
        self.ax = pg.PlotItem()
        self.ax.invertY(True)
        self.ax.setAspectLocked(True)
        self.ax.hideAxis('bottom')
        self.ax.hideAxis('left')
        self.graphLayout.addItem(self.ax, row=1, col=1)

        # Image Item
        self.imgItem = pg.ImageItem(np.zeros((512,512)))
        self.ax.addItem(self.imgItem)

        #Image histogram
        self.hist = pg.HistogramLUTItem()

        # Curvature items
        self.hoverLinSpace = np.linspace(0, 1, 1000)
        self.hoverLinePen = pg.mkPen(color=(200, 0, 0, 255*0.5),
                                     width=2, style=Qt.DashLine)
        self.hoverCurvePen = pg.mkPen(color=(200, 0, 0, 255*0.5), width=3)
        self.lineHoverPlotItem = pg.PlotDataItem(pen=self.hoverLinePen)
        self.curvHoverPlotItem = pg.PlotDataItem(pen=self.hoverCurvePen)
        self.curvAnchors = pg.ScatterPlotItem(
            symbol='o', size=9,
            brush=pg.mkBrush((255,0,0,50)),
            pen=pg.mkPen((255,0,0), width=2),
            hoverable=True, hoverPen=pg.mkPen((255,0,0), width=3),
            hoverBrush=pg.mkBrush((255,0,0))
        )
        self.ax.addItem(self.curvAnchors)
        self.ax.addItem(self.curvHoverPlotItem)
        self.ax.addItem(self.lineHoverPlotItem)

    def gui_createImgWidgets(self):
        self.img_Widglayout = QtGui.QGridLayout()
        self.img_Widglayout.setContentsMargins(50, 0, 50, 0)

        alphaScrollBar_label = QLabel('Overlay alpha  ')
        alphaScrollBar = QScrollBar(Qt.Horizontal)
        alphaScrollBar.setFixedHeight(20)
        alphaScrollBar.setMinimum(0)
        alphaScrollBar.setMaximum(40)
        alphaScrollBar.setValue(12)
        alphaScrollBar.setToolTip(
            'Control the alpha value of the overlay.\n'
            'alpha=0 results in NO overlay,\n'
            'alpha=1 results in only labels visible'
        )
        alphaScrollBar.sliderMoved.connect(self.alphaScrollBarMoved)
        self.alphaScrollBar = alphaScrollBar
        self.alphaScrollBar_label = alphaScrollBar_label
        self.img_Widglayout.addWidget(
            alphaScrollBar_label, 0, 0, alignment=Qt.AlignCenter
        )
        self.img_Widglayout.addWidget(alphaScrollBar, 0, 1, 1, 20)
        self.alphaScrollBar.hide()
        self.alphaScrollBar_label.hide()

    def gui_connectImgActions(self):
        self.imgItem.hoverEvent = self.gui_hoverEventImg
        self.imgItem.mousePressEvent = self.gui_mousePressEventImg
        self.imgItem.mouseMoveEvent = self.gui_mouseDragEventImg
        self.imgItem.mouseReleaseEvent = self.gui_mouseReleaseEventImg

    def gui_hoverEventImg(self, event):
        # Update x, y, value label bottom right
        try:
            x, y = event.pos()
            xdata, ydata = int(x), int(y)
            _img = self.lab
            Y, X = _img.shape
            if xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y:
                val = _img[ydata, xdata]
                self.wcLabel.setText(f'(x={x:.2f}, y={y:.2f}, ID={val:.0f})')
            else:
                self.wcLabel.setText(f'')
        except Exception as e:
            self.wcLabel.setText(f'')

        try:
            if not event.isExit():
                x, y = event.pos()
                if self.countClicks == 1:
                    self.lineHoverPlotItem.setData([self.x0, x], [self.y0, y])
                elif self.countClicks == 2:
                    xx = [self.x0, x, self.x1]
                    yy = [self.y0, y, self.y1]
                    xi, yi = self.getSpline(xx, yy)
                    self.curvHoverPlotItem.setData(xi, yi)
                elif self.countClicks == 0:
                    self.curvHoverPlotItem.setData([], [])
                    self.lineHoverPlotItem.setData([], [])
                    self.curvAnchors.setData([], [])
        except Exception as e:
            traceback.print_exc()
            pass

    def getSpline(self, xx, yy):
        tck, u = scipy.interpolate.splprep([xx, yy], s=0, k=2)
        xi, yi = scipy.interpolate.splev(self.hoverLinSpace, tck)
        return xi, yi


    def gui_mousePressEventImg(self, event):
        right_click = event.button() == Qt.MouseButton.RightButton
        left_click = event.button() == Qt.MouseButton.LeftButton

        dragImg = (left_click)

        if dragImg:
            pg.ImageItem.mousePressEvent(self.imgItem, event)

        if right_click and self.countClicks == 0:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            self.x0, self.y0 = xdata, ydata
            self.curvAnchors.addPoints([xdata], [ydata])
            self.countClicks = 1
        elif right_click and self.countClicks == 1:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            self.x1, self.y1 = xdata, ydata
            self.curvAnchors.addPoints([xdata], [ydata])
            self.countClicks = 2
        elif right_click and self.countClicks == 2:
            self.storeUndoState()
            self.countClicks = 0
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            xx = [self.x0, xdata, self.x1]
            yy = [self.y0, ydata, self.y1]
            xi, yi = self.getSpline(xx, yy)
            yy, xx = np.round(yi).astype(int), np.round(xi).astype(int)
            xxCurve, yyCurve = [], []
            for i, (r0, c0) in enumerate(zip(yy, xx)):
                if i == len(yy)-1:
                    break
                r1 = yy[i+1]
                c1 = xx[i+1]
                rr, cc, _ = skimage.draw.line_aa(r0, c0, r1, c1)
                # rr, cc = skimage.draw.line(r0, c0, r1, c1)
                nonzeroMask = self.lab[rr, cc]>0
                xxCurve.extend(cc[nonzeroMask])
                yyCurve.extend(rr[nonzeroMask])
            self.AllCutsCoords.append((yyCurve, xxCurve))
            for rr, cc in self.AllCutsCoords:
                self.lab[rr, cc] = 0
            self.updateLabels()


    def histLUT_cb(self, LUTitem):
        if self.overlayButton.isChecked():
            overlay = self.getOverlay()
            self.imgItem.setImage(overlay)

    def updateImg(self):
        self.updateLookuptable()
        rp = skimage.measure.regionprops(self.lab)
        self.rp = rp

        if self.overlayButton.isChecked():
            overlay = self.getOverlay()
            self.imgItem.setImage(overlay)
        else:
            self.imgItem.setImage(self.lab)

        # Draw ID on centroid of each label
        for labelItemID in self.labelItemsIDs:
            self.ax.removeItem(labelItemID)
        self.labelItemsIDs = []
        for obj in rp:
            labelItemID = pg.LabelItem()
            labelItemID.setText(f'{obj.label}', color='r', size=self.fontSize)
            y, x = obj.centroid
            w, h = labelItemID.rect().right(), labelItemID.rect().bottom()
            labelItemID.setPos(x-w/2, y-h/2)
            self.labelItemsIDs.append(labelItemID)
            self.ax.addItem(labelItemID)

    def zoomToObj(self):
        # Zoom to object
        lab_mask = (self.lab>0).astype(np.uint8)
        rp = skimage.measure.regionprops(lab_mask)
        obj = rp[0]
        min_row, min_col, max_row, max_col = obj.bbox
        xRange = min_col-10, max_col+10
        yRange = max_row+10, min_row-10
        self.ax.setRange(xRange=xRange, yRange=yRange)

    def storeUndoState(self):
        self.prevLabs.append(self.lab.copy())
        self.prevAllCutsCoords.append(self.AllCutsCoords.copy())
        self.undoIdx += 1
        self.undoAction.setEnabled(True)

    def undo(self):
        self.undoIdx -= 1
        self.lab = self.prevLabs[self.undoIdx]
        self.AllCutsCoords = self.prevAllCutsCoords[self.undoIdx]
        self.updateImg()
        if self.undoIdx == 0:
            self.undoAction.setEnabled(False)
            self.prevLabs = []
            self.prevAllCutsCoords = []


    def updateLabels(self):
        self.lab = skimage.measure.label(self.lab, connectivity=1)

        # Relabel largest object with original ID
        rp = skimage.measure.regionprops(self.lab)
        areas = [obj.area for obj in rp]
        IDs = [obj.label for obj in rp]
        maxAreaIdx = areas.index(max(areas))
        maxAreaID = IDs[maxAreaIdx]
        if self.ID not in self.lab:
            self.lab[self.lab==maxAreaID] = self.ID

        if self.parent is not None:
            self.parent.setBrushID()
        # Use parent window setBrushID function for all other IDs
        for i, obj in enumerate(rp):
            if self.parent is None:
                break
            if i == maxAreaIdx:
                continue
            PosData = self.parent.data[self.parent.pos_i]
            PosData.brushID += 1
            self.lab[obj.slice][obj.image] = PosData.brushID


        # Replace 0s on the cutting curve with IDs
        self.cutLab = self.lab.copy()
        for rr, cc in self.AllCutsCoords:
            for y, x in zip(rr, cc):
                top_row = self.cutLab[y+1, x-1:x+2]
                bot_row = self.cutLab[y-1, x-1:x+1]
                left_col = self.cutLab[y-1, x-1]
                right_col = self.cutLab[y:y+2, x+1]
                allNeigh = list(top_row)
                allNeigh.extend(bot_row)
                allNeigh.append(left_col)
                allNeigh.extend(right_col)
                newID = max(allNeigh)
                self.lab[y,x] = newID

        self.rp = skimage.measure.regionprops(self.lab)
        self.updateImg()

    def updateLookuptable(self):
        # Lookup table
        self.cmap = myutils.getFromMatplotlib('viridis')
        self.lut = self.cmap.getLookupTable(0,1,self.lab.max()+1)
        self.lut[0] = [25,25,25]
        self.lut[self.ID] = self.IDcolor
        if self.overlayButton.isChecked():
            self.imgItem.setLookupTable(None)
        else:
            self.imgItem.setLookupTable(self.lut)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.countClicks = 0
            self.curvHoverPlotItem.setData([], [])
            self.lineHoverPlotItem.setData([], [])
            self.curvAnchors.setData([], [])
        elif ev.key() == Qt.Key_Enter or ev.key() == Qt.Key_Return:
            self.ok_cb()

    def getOverlay(self):
        # Rescale intensity based on hist ticks values
        min = self.hist.gradient.listTicks()[0][1]
        max = self.hist.gradient.listTicks()[1][1]
        img = skimage.exposure.rescale_intensity(
                                      self.img, in_range=(min, max))
        alpha = self.alphaScrollBar.value()/self.alphaScrollBar.maximum()

        # Convert img and lab to RGBs
        rgb_shape = (self.lab.shape[0], self.lab.shape[1], 3)
        labRGB = np.zeros(rgb_shape)
        labRGB[self.lab>0] = [1, 1, 1]
        imgRGB = skimage.color.gray2rgb(img)
        overlay = imgRGB*(1.0-alpha) + labRGB*alpha

        # Color eaach label
        for obj in self.rp:
            rgb = self.lut[obj.label]/255
            overlay[obj.slice][obj.image] *= rgb

        # Convert (0,1) to (0,255)
        overlay = (np.clip(overlay, 0, 1)*255).astype(np.uint8)
        return overlay


    def gui_mouseDragEventImg(self, event):
        pass

    def gui_mouseReleaseEventImg(self, event):
        pass

    def alphaScrollBarMoved(self, alpha_int):
        overlay = self.getOverlay()
        self.imgItem.setImage(overlay)

    def toggleOverlay(self, checked):
        if checked:
            self.graphLayout.addItem(self.hist, row=1, col=0)
            self.alphaScrollBar.show()
            self.alphaScrollBar_label.show()
        else:
            self.graphLayout.removeItem(self.hist)
            self.alphaScrollBar.hide()
            self.alphaScrollBar_label.hide()
        self.updateImg()

    def help(self):
        msg = QtGui.QMessageBox()
        msg.information(self, 'Help',
            'Separate object along a curved line.\n\n'
            'To draw a curved line you will need 3 right-clicks:\n\n'
            '1. Right-click outside of the object --> a line appears.\n'
            '2. Right-click to end the line and a curve going through the '
            'mouse cursor will appear.\n'
            '3. Once you are happy with the cutting curve right-click again '
            'and the object will be separated along the curve.\n\n'
            'Note that you can separate as many times as you want.\n\n'
            'Once happy click on the green tick on top-right or '
            'cancel the process with the "X" button')

    def ok_cb(self, checked):
        self.cancel = False
        self.close()

    def closeEvent(self, event):
        if self.loop is not None:
            self.loop.exit()

class DataFrameModel(QtCore.QAbstractTableModel):
    # https://stackoverflow.com/questions/44603119/how-to-display-a-pandas-data-frame-with-pyqt5-pyside2
    DtypeRole = QtCore.Qt.UserRole + 1000
    ValueRole = QtCore.Qt.UserRole + 1001

    def __init__(self, df=pd.DataFrame(), parent=None):
        super(DataFrameModel, self).__init__(parent)
        self._dataframe = df

    def setDataFrame(self, dataframe):
        self.beginResetModel()
        self._dataframe = dataframe.copy()
        self.endResetModel()

    def dataFrame(self):
        return self._dataframe

    dataFrame = QtCore.pyqtProperty(pd.DataFrame, fget=dataFrame,
                                    fset=setDataFrame)

    @QtCore.pyqtSlot(int, QtCore.Qt.Orientation, result=str)
    def headerData(self, section: int,
                   orientation: QtCore.Qt.Orientation,
                   role: int = QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole:
            if orientation == QtCore.Qt.Horizontal:
                return self._dataframe.columns[section]
            else:
                return str(self._dataframe.index[section])
        return QtCore.QVariant()

    def rowCount(self, parent=QtCore.QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._dataframe.index)

    def columnCount(self, parent=QtCore.QModelIndex()):
        if parent.isValid():
            return 0
        return self._dataframe.columns.size

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < self.rowCount() \
            and 0 <= index.column() < self.columnCount()):
            return QtCore.QVariant()
        row = self._dataframe.index[index.row()]
        col = self._dataframe.columns[index.column()]
        dt = self._dataframe[col].dtype

        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter

        val = self._dataframe.iloc[row][col]
        if role == QtCore.Qt.DisplayRole:
            return str(val)
        elif role == DataFrameModel.ValueRole:
            return val
        if role == DataFrameModel.DtypeRole:
            return dt
        return QtCore.QVariant()

    def roleNames(self):
        roles = {
            QtCore.Qt.DisplayRole: b'display',
            DataFrameModel.DtypeRole: b'dtype',
            DataFrameModel.ValueRole: b'value'
        }
        return roles

class pdDataFrameWidget(QMainWindow):
    def __init__(self, df, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setWindowTitle('Cell cycle annotations')

        mainContainer = QtGui.QWidget()
        self.setCentralWidget(mainContainer)

        layout = QVBoxLayout()

        self.tableView = QTableView(self)
        layout.addWidget(self.tableView)
        model = DataFrameModel(df)
        self.tableView.setModel(model)
        for i in range(len(df.columns)):
            self.tableView.resizeColumnToContents(i)
        # layout.addWidget(QPushButton('Ok', self))
        mainContainer.setLayout(layout)

    def updateTable(self, df):
        if df is None:
            df = self.parent.getBaseCca_df()
        df = df.reset_index()
        model = DataFrameModel(df)
        self.tableView.setModel(model)
        for i in range(len(df.columns)):
            self.tableView.resizeColumnToContents(i)

    def setGeometryWindow(self, maxWidth=1024):
        width = self.tableView.verticalHeader().width() + 4
        for j in range(self.tableView.model().columnCount()):
            width += self.tableView.columnWidth(j) + 4
        height = self.tableView.horizontalHeader().height() + 4
        h = height + (self.tableView.rowHeight(0) + 4)*10
        w = width if width<maxWidth else maxWidth
        self.setGeometry(100, 100, w, h)

        # Center window
        parent = self.parent
        if parent is not None:
            # Center the window on main window
            mainWinGeometry = parent.geometry()
            mainWinLeft = mainWinGeometry.left()
            mainWinTop = mainWinGeometry.top()
            mainWinWidth = mainWinGeometry.width()
            mainWinHeight = mainWinGeometry.height()
            mainWinCenterX = int(mainWinLeft + mainWinWidth/2)
            mainWinCenterY = int(mainWinTop + mainWinHeight/2)
            winGeometry = self.geometry()
            winWidth = winGeometry.width()
            winHeight = winGeometry.height()
            winLeft = int(mainWinCenterX - winWidth/2)
            winRight = int(mainWinCenterY - winHeight/2)
            self.move(winLeft, winRight)

    def closeEvent(self, event):
        self.parent.ccaTableWin = None

class QDialogZsliceAbsent(QDialog):
    def __init__(self, filename, SizeZ, filenamesWithInfo, parent=None):
        self.runDataPrep = False
        self.useMiddleSlice = False
        self.useSameAsCh = False

        super().__init__(parent)
        self.setWindowTitle('z-slice info absent!')

        mainLayout = QVBoxLayout()
        buttonsLayout = QVBoxLayout()
        comboBoxLayout = QHBoxLayout()

        txt = (
        f"""
        <p style="font-size:11pt; text-align: center;">
            You loaded the fluorescent file called {filename},<br>
            however you <b>never selected which z-slice</b> you want to use
            when calculating metrics<br> (e.g., mean, median, amount...etc.)<br><br>
            Choose one of following options:
        <p>
        """
        )
        infoLabel = QLabel(txt)
        mainLayout.addWidget(infoLabel, alignment=Qt.AlignCenter)

        runDataPrepButton = QPushButton(
            'Visualize the data now and select a z-slice (RECOMMENDED)'
        )
        buttonsLayout.addWidget(runDataPrepButton)
        runDataPrepButton.clicked.connect(self.runDataPrep_cb)

        useMiddleSliceButton = QPushButton(
            f'Use the middle z-slice ({int(SizeZ/2)})'
        )
        buttonsLayout.addWidget(useMiddleSliceButton)
        useMiddleSliceButton.clicked.connect(self.useMiddleSlice_cb)

        useSameAsChButton = QPushButton(
            'Use the same z-slice used for the channel: '
        )
        useSameAsChButton.clicked.connect(self.useSameAsCh_cb)

        chNameComboBox = QComboBox()
        chNameComboBox.addItems(filenamesWithInfo)
        # chNameComboBox.setEditable(True)
        # chNameComboBox.lineEdit().setAlignment(Qt.AlignCenter)
        # chNameComboBox.lineEdit().setReadOnly(True)
        self.chNameComboBox = chNameComboBox
        comboBoxLayout.addWidget(useSameAsChButton)
        comboBoxLayout.addWidget(chNameComboBox)
        buttonsLayout.addLayout(comboBoxLayout)
        buttonsLayout.setContentsMargins(10, 0, 10, 0)

        mainLayout.addLayout(buttonsLayout)

        self.setLayout(mainLayout)

        font = QtGui.QFont()
        font.setPointSize(10)
        self.setFont(font)

        self.setModal(True)

    def useSameAsCh_cb(self, checked):
        self.useSameAsCh = True
        self.selectedChannel = self.chNameComboBox.currentText()
        self.close()

    def useMiddleSlice_cb(self, checked):
        self.useMiddleSlice = True
        self.close()

    def runDataPrep_cb(self, checked):
        self.runDataPrep = True
        self.close()

if __name__ == '__main__':
    # Create the application
    app = QApplication(sys.argv)
    font = QtGui.QFont()
    font.setPointSize(10)
    # title='Select channel name'
    # CbLabel='Select channel name:  '
    # informativeText = ''
    # win = QtSelectItems(title, ['mNeon', 'mKate'],
    #                     informativeText, CbLabel=CbLabel, parent=None)
    # win = edgeDetectionDialog(None)
    # win = QDialogEntriesWidget(entriesLabels=['Input 1'])
    IDs = list(range(1,10))
    cc_stage = ['G1' for ID in IDs]
    num_cycles = [-1]*len(IDs)
    relationship = ['mother' for ID in IDs]
    related_to = [-1]*len(IDs)
    is_history_known = [False]*len(IDs)
    corrected_assignment = [False]*len(IDs)
    cca_df = pd.DataFrame({
                       'cell_cycle_stage': cc_stage,
                       'generation_num': num_cycles,
                       'relative_ID': related_to,
                       'relationship': relationship,
                       'emerg_frame_i': num_cycles,
                       'division_frame_i': num_cycles,
                       'is_history_known': is_history_known,
                       'corrected_assignment': corrected_assignment},
                        index=IDs)
    cca_df.index.name = 'Cell_ID'
    #
    # df = cca_df.reset_index()
    #
    # win = pdDataFrameWidget(df)
    # win = QDialogMetadataXML(rawDataStruct=1, chNames=[''])
    # win = QDialogAppendTextFilename('example.npz')
    font = QtGui.QFont()
    font.setPointSize(10)
    filenames = ['test1', 'test2']
    win = QDialogZsliceAbsent('test3', 30, filenames)
    # win = cellpose_ParamsDialog()
    # user_ch_file_paths = [
    #     r"G:\My Drive\1_MIA_Data\Beno\test_QtGui\testGuiOnlyTifs\TIFFs\Position_1\Images\19-03-2021_KCY050_SCGE_s02_phase_contr.tif",
    #     r"G:\My Drive\1_MIA_Data\Beno\test_QtGui\testGuiOnlyTifs\TIFFs\Position_2\Images\19-03-2021_KCY050_SCGE_s02_phase_contr.tif"
    # ]
    # user_ch_name = 'phase_contr'
    # win = askStopFrameSegm(user_ch_file_paths, user_ch_name)
    # lab = np.load(r"G:\My Drive\1_MIA_Data\Test_data\Test_Qt_GUI\Position_5\Images\F016_s05_segm.npz")['arr_0'][0]
    # img = np.load(r"G:\My Drive\1_MIA_Data\Test_data\Test_Qt_GUI\Position_5\Images\F016_s05_phase_contr_aligned.npz")['arr_0'][0]
    # win = manualSeparateGui(lab, 2, img)
    win.setFont(font)
    app.setStyle(QtGui.QStyleFactory.create('Fusion'))
    # win.showAndSetWidth()
    # win.showAndSetFont(font)
    # win.setWidths(font=font)
    # win.setSize()
    # win.setGeometryWindow()
    win.exec_()
    print(win.chNames, win.saveChannels)
    # print(win.SizeT, win.SizeZ, win.zyx_vox_dim)
