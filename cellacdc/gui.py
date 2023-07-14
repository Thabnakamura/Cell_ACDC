print('Importing GUI modules...')
import sys
import os
import shutil
import pathlib
import re
import traceback
import time
import datetime
import inspect
import logging
import uuid
import json
import pprint
import psutil
from importlib import import_module
from functools import partial
from tqdm import tqdm
from natsort import natsorted
import time
import cv2
import math
import numpy as np
import pandas as pd
import matplotlib
import scipy.optimize
import scipy.interpolate
import scipy.ndimage
import skimage
import skimage.io
import skimage.measure
import skimage.morphology
import skimage.draw
import skimage.exposure
import skimage.transform
import skimage.segmentation

from PIL import Image, ImageFont, ImageDraw

from functools import wraps
from skimage.color import gray2rgb, gray2rgba, label2rgb

from qtpy.QtCore import (
    Qt, QFile, QTextStream, QSize, QRect, QRectF,
    QEventLoop, QTimer, QEvent, QObject, Signal,
    QThread, QMutex, QWaitCondition, QSettings
)
from qtpy.QtGui import (
    QIcon, QKeySequence, QCursor, QGuiApplication, QPixmap, QColor,
    QFont
)
from qtpy.QtWidgets import (
    QAction, QLabel, QPushButton, QHBoxLayout, QSizePolicy,
    QMainWindow, QMenu, QToolBar, QGroupBox, QGridLayout,
    QScrollBar, QCheckBox, QToolButton, QSpinBox,
    QComboBox, QButtonGroup, QActionGroup, QFileDialog,
    QAbstractSlider, QMessageBox, QWidget, QGridLayout, QDockWidget,
    QGraphicsProxyWidget, QVBoxLayout, QRadioButton, 
    QSpacerItem, QScrollArea, QFormLayout
)

import pyqtgraph as pg

# NOTE: Enable icons
from . import qrc_resources

# Custom modules
from . import exception_handler
from . import base_cca_df, graphLayoutBkgrColor, darkBkgrColor
from . import load, prompts, apps, workers, html_utils
from . import core, myutils, dataPrep, widgets
from . import _warnings
from . import measurements, printl
from . import colors, filters, annotate
from . import user_manual_url
from . import recentPaths_path, settings_folderpath, settings_csv_path
from . import qutils, autopilot, QtScoped
from .trackers.CellACDC import CellACDC_tracker
from .cca_functions import _calc_rot_vol
from .myutils import exec_time, setupLogger
from .help import welcome, about

if os.name == 'nt':
    try:
        # Set taskbar icon in windows
        import ctypes
        myappid = 'schmollerlab.cellacdc.pyqt.v1' # arbitrary string
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception as e:
        pass

favourite_func_metrics_csv_path = os.path.join(
    settings_folderpath, 'favourite_func_metrics.csv'
)
custom_annot_path = os.path.join(settings_folderpath, 'custom_annotations.json')
shortcut_filepath = os.path.join(settings_folderpath, 'shortcuts.ini')

_font = QFont()
_font.setPixelSize(11)

SliderSingleStepAdd = QtScoped.SliderSingleStepAdd()
SliderSingleStepSub = QtScoped.SliderSingleStepSub()
SliderPageStepAdd = QtScoped.SliderPageStepAdd()
SliderPageStepSub = QtScoped.SliderPageStepSub()
SliderMove = QtScoped.SliderMove()

def qt_debug_trace():
    from qtpy.QtCore import pyqtRemoveInputHook
    pyqtRemoveInputHook()
    import pdb; pdb.set_trace()

def get_data_exception_handler(func):
    @wraps(func)
    def inner_function(self, *args, **kwargs):
        try:
            if func.__code__.co_argcount==1 and func.__defaults__ is None:
                result = func(self)
            elif func.__code__.co_argcount>1 and func.__defaults__ is None:
                result = func(self, *args)
            else:
                result = func(self, *args, **kwargs)
        except Exception as e:
            try:
                if self.progressWin is not None:
                    self.progressWin.workerFinished = True
                    self.progressWin.close()
            except AttributeError:
                pass
            result = None
            posData = self.data[self.pos_i]
            acdc_df_filename = os.path.basename(posData.acdc_output_csv_path)
            segm_filename = os.path.basename(posData.segm_npz_path)
            traceback_str = traceback.format_exc()
            self.logger.exception(traceback_str)
            msg = widgets.myMessageBox(wrapText=False, showCentered=False)
            msg.addShowInFileManagerButton(self.logs_path, txt='Show log file...')
            msg.setDetailedText(traceback_str)
            err_msg = html_utils.paragraph(f"""
                Error in function <code>{func.__name__}</code>.<br><br>
                One possbile explanation is that either the
                <code>{acdc_df_filename}</code> file<br>
                or the segmentation file <code>{segm_filename}</code><br>
                <b>are corrupted/damaged</b>.<br><br>
                <b>Try moving these files</b> (one by one) outside of the
                <code>{os.path.dirname(posData.relPath)}</code> folder
                <br>and reloading the data.<br><br>
                More details below or in the terminal/console.<br><br>
                Note that the <b>error details</b> from this session are
                also <b>saved in the following file</b>:<br><br>
                {self.log_path}<br><br>
                Please <b>send the log file</b> when reporting a bug, thanks!
            """)

            msg.critical(self, 'Critical error', err_msg)
            self.is_error_state = True
            raise e
        return result
    return inner_function

class relabelSequentialWorker(QObject):
    finished = Signal()
    critical = Signal(object)
    progress = Signal(str)
    sigRemoveItemsGUI = Signal(int)
    debug = Signal(object)

    def __init__(self, posData, mainWin):
        QObject.__init__(self)
        self.mainWin = mainWin
        self.posData = posData
        self.mutex = QMutex()
        self.waitCond = QWaitCondition()

    def progressNewIDs(self, inv):
        newIDs = inv.in_values
        oldIDs = inv.out_values
        li = list(zip(oldIDs, newIDs))
        s = '\n'.join([str(pair).replace(',', ' -->') for pair in li])
        s = f'IDs relabelled as follows:\n{s}'
        self.progress.emit(s)

    @workers.worker_exception_handler
    def run(self):
        self.mutex.lock()

        self.progress.emit('Relabelling process started...')

        posData = self.posData
        progressWin = self.mainWin.progressWin
        mainWin = self.mainWin

        current_lab = self.mainWin.get_2Dlab(posData.lab).copy()
        current_frame_i = posData.frame_i
        segm_data = []
        for frame_i, data_dict in enumerate(posData.allData_li):
            lab = data_dict['labels']
            if lab is None:
                break
            segm_data.append(lab)
            if frame_i == current_frame_i:
                break

        if not segm_data:
            segm_data = np.array(current_lab)

        segm_data = np.array(segm_data)
        segm_data, fw, inv = skimage.segmentation.relabel_sequential(
            segm_data
        )
        self.progressNewIDs(inv)
        self.sigRemoveItemsGUI.emit(np.max(segm_data))

        self.progress.emit(
            'Updating stored data and cell cycle annotations '
            '(if present)...'
        )
        newIDs = list(inv.in_values)
        oldIDs = list(inv.out_values)
        newIDs.append(-1)
        oldIDs.append(-1)

        mainWin.updateAnnotatedIDs(oldIDs, newIDs, logger=self.progress.emit)
        mainWin.store_data(mainThread=False)

        for frame_i, lab in enumerate(segm_data):
            posData.frame_i = frame_i
            posData.lab = lab
            mainWin.get_cca_df()
            if posData.cca_df is not None:
                mainWin.update_cca_df_relabelling(
                    posData, oldIDs, newIDs
                )
            mainWin.update_rp(draw=False)
            mainWin.store_data(mainThread=False)


        # Go back to current frame
        posData.frame_i = current_frame_i
        mainWin.get_data()

        self.mutex.unlock()
        self.finished.emit()

class saveDataWorker(QObject):
    finished = Signal()
    progress = Signal(str)
    progressBar = Signal(int, int, float)
    critical = Signal(object)
    addMetricsCritical = Signal(str, str)
    regionPropsCritical = Signal(str, str)
    criticalPermissionError = Signal(str)
    metricsPbarProgress = Signal(int, int)
    askZsliceAbsent = Signal(str, object)
    customMetricsCritical = Signal(str, str)
    sigCombinedMetricsMissingColumn = Signal(str, str)
    sigDebug = Signal(object)

    def __init__(self, mainWin):
        QObject.__init__(self)
        self.mainWin = mainWin
        self.saveWin = mainWin.saveWin
        self.mutex = mainWin.mutex
        self.waitCond = mainWin.waitCond
        self.customMetricsErrors = {}
        self.addMetricsErrors = {}
        self.regionPropsErrors = {}
        self.abort = False
    
    def _check_zSlice(self, posData, frame_i):
        if posData.SizeZ == 1:
            return True
        
        # Iteare fluo channels and get 2D data from 3D if needed
        filenames = posData.fluo_data_dict.keys()
        for chName, filename in zip(posData.loadedChNames, filenames):
            idx = (filename, frame_i)
            try:
                if posData.segmInfo_df.at[idx, 'resegmented_in_gui']:
                    col = 'z_slice_used_gui'
                else:
                    col = 'z_slice_used_dataPrep'
                z_slice = posData.segmInfo_df.at[idx, col]
            except KeyError:
                try:
                    # Try to see if the user already selected z-slice in prev pos
                    segmInfo_df = pd.read_csv(posData.segmInfo_df_csv_path)
                    index_col = ['filename', 'frame_i']
                    posData.segmInfo_df = segmInfo_df.set_index(index_col)
                    col = 'z_slice_used_dataPrep'
                    z_slice = posData.segmInfo_df.at[idx, col]
                except KeyError as e:
                    self.progress.emit(
                        f'z-slice for channel "{chName}" absent. '
                        'Follow instructions on pop-up dialogs.'
                    )
                    self.mutex.lock()
                    self.askZsliceAbsent.emit(filename, posData)
                    self.waitCond.wait(self.mutex)
                    self.mutex.unlock()
                    if self.abort:
                        return False
                    self.progress.emit(
                        f'Saving (check terminal for additional progress info)...'
                    )
                    segmInfo_df = pd.read_csv(posData.segmInfo_df_csv_path)
                    index_col = ['filename', 'frame_i']
                    posData.segmInfo_df = segmInfo_df.set_index(index_col)
                    col = 'z_slice_used_dataPrep'
                    z_slice = posData.segmInfo_df.at[idx, col]
        return True
    
    def _emitSigDebug(self, stuff_to_debug):
        self.mutex.lock()
        self.sigDebug.emit(stuff_to_debug)
        self.waitCond.wait(self.mutex)
        self.mutex.unlock()

    def addMetrics_acdc_df(self, stored_df, rp, frame_i, lab, posData):
        yx_pxl_to_um2 = posData.PhysicalSizeY*posData.PhysicalSizeX
        vox_to_fl_3D = (
            posData.PhysicalSizeY*posData.PhysicalSizeX*posData.PhysicalSizeZ
        )

        isZstack = posData.SizeZ > 1
        isSegm3D = self.mainWin.isSegm3D
        all_channels_metrics = self.mainWin.metricsToSave
        size_metrics_to_save = self.mainWin.sizeMetricsToSave
        regionprops_to_save = self.mainWin.regionPropsToSave
        metrics_func = self.mainWin.metrics_func
        custom_func_dict = self.mainWin.custom_func_dict
        bkgr_metrics_params = self.mainWin.bkgr_metrics_params
        foregr_metrics_params = self.mainWin.foregr_metrics_params
        concentration_metrics_params = self.mainWin.concentration_metrics_params
        custom_metrics_params = self.mainWin.custom_metrics_params

        # Pre-populate columns with zeros
        all_columns = list(size_metrics_to_save)
        for channel, metrics in all_channels_metrics.items():
            all_columns.extend(metrics)
        all_columns.extend(regionprops_to_save)

        df_shape = (len(stored_df), len(all_columns))
        data = np.zeros(df_shape)
        df = pd.DataFrame(data=data, index=stored_df.index, columns=all_columns)
        df = df.combine_first(stored_df)

        # Check if z-slice is present for 3D z-stack data
        proceed = self._check_zSlice(posData, frame_i)
        if not proceed:
            return

        df = measurements.add_size_metrics(
            df, rp, size_metrics_to_save, isSegm3D, yx_pxl_to_um2, 
            vox_to_fl_3D
        )
        
        # Get background masks
        autoBkgr_masks = measurements.get_autoBkgr_mask(
            lab, isSegm3D, posData, frame_i
        )
        # self._emitSigDebug((lab, frame_i, autoBkgr_masks))
        
        autoBkgr_mask, autoBkgr_mask_proj = autoBkgr_masks
        dataPrepBkgrROI_mask = measurements.get_bkgrROI_mask(posData, isSegm3D)

        # Iterate channels
        iter_channels = zip(posData.loadedChNames, posData.fluo_data_dict.items())
        for channel, (filename, channel_data) in iter_channels:
            foregr_img = channel_data[frame_i]

            # Get the z-slice if we have z-stacks
            z = posData.zSliceSegmentation(filename, frame_i)
            
            # Get the background data
            bkgr_data = measurements.get_bkgr_data(
                foregr_img, posData, filename, frame_i, autoBkgr_mask, z,
                autoBkgr_mask_proj, dataPrepBkgrROI_mask, isSegm3D, lab
            )

            # Compute background values
            df = measurements.add_bkgr_values(
                df, bkgr_data, bkgr_metrics_params[channel], metrics_func
            )
            
            foregr_data = measurements.get_foregr_data(foregr_img, isSegm3D, z)

            # Iterate objects and compute foreground metrics
            df = measurements.add_foregr_metrics(
                df, rp, channel, foregr_data, foregr_metrics_params[channel], 
                metrics_func, custom_metrics_params[channel], isSegm3D, 
                yx_pxl_to_um2, vox_to_fl_3D, lab, foregr_img,
                customMetricsCritical=self.customMetricsCritical
            )

        df = measurements.add_concentration_metrics(
            df, concentration_metrics_params
        )

        # Add region properties
        try:
            df, rp_errors = measurements.add_regionprops_metrics(
                df, lab, regionprops_to_save, logger_func=self.progress.emit
            )
            if rp_errors:
                print('')
                self.progress.emit(
                    'WARNING: Some objects had the following errors:\n'
                    f'{rp_errors}\n'
                    'Region properties with errors were saved as `Not A Number`.'
                )
        except Exception as error:
            traceback_format = traceback.format_exc()
            self.regionPropsCritical.emit(traceback_format, str(error))

        # Remove 0s columns
        df = df.loc[:, (df != -2).any(axis=0)]

        return df

    def _dfEvalEquation(self, df, newColName, expr):
        try:
            df[newColName] = df.eval(expr)
        except pd.errors.UndefinedVariableError as error:
            self.sigCombinedMetricsMissingColumn.emit(str(error), newColName)
        
        try:
             df[newColName] = df.eval(expr)
        except Exception as error:
            self.customMetricsCritical.emit(
                traceback.format_exc(), newColName
            )

    def _removeDeprecatedRows(self, df):
        v1_2_4_rc25_deprecated_cols = [
            'editIDclicked_x', 'editIDclicked_y',
            'editIDnewID', 'editIDnewIDs'
        ]
        df = df.drop(columns=v1_2_4_rc25_deprecated_cols, errors='ignore')

        # Remove old gui_ columns from version < v1.2.4.rc-7
        gui_columns = df.filter(regex='gui_*').columns
        df = df.drop(columns=gui_columns, errors='ignore')
        cell_id_cols = df.filter(regex='Cell_ID.*').columns
        df = df.drop(columns=cell_id_cols, errors='ignore')
        time_seconds_cols = df.filter(regex='time_seconds.*').columns
        df = df.drop(columns=time_seconds_cols, errors='ignore')
        df = df.drop(columns='relative_ID_tree', errors='ignore')

        return df

    def addCombineMetrics_acdc_df(self, posData, df):
        # Add channel specifc combined metrics (from equations and 
        # from user_path_equations sections)
        config = posData.combineMetricsConfig
        for chName in posData.loadedChNames:
            metricsToSkipChannel = self.mainWin.metricsToSkip.get(chName, [])
            posDataEquations = config['equations']
            userPathChEquations = config['user_path_equations']
            for newColName, equation in posDataEquations.items():
                if newColName in metricsToSkipChannel:
                    continue
                self._dfEvalEquation(df, newColName, equation)
            for newColName, equation in userPathChEquations.items():
                if newColName in metricsToSkipChannel:
                    continue
                self._dfEvalEquation(df, newColName, equation)

        # Add mixed channels combined metrics
        mixedChannelsEquations = config['mixed_channels_equations']
        for newColName, equation in mixedChannelsEquations.items():
            if newColName in self.mainWin.mixedChCombineMetricsToSkip:
                continue
            cols = re.findall(r'[A-Za-z0-9]+_[A-Za-z0-9_]+', equation)
            if all([col in df.columns for col in cols]):
                self._dfEvalEquation(df, newColName, equation)
    
    def addVelocityMeasurement(self, acdc_df, prev_lab, lab, posData):
        if 'velocity_pixel' not in self.mainWin.sizeMetricsToSave:
            return acdc_df
        
        if 'velocity_um' not in self.mainWin.sizeMetricsToSave:
            spacing = None 
        elif self.mainWin.isSegm3D:
            spacing = np.array([
                posData.PhysicalSizeZ, 
                posData.PhysicalSizeY, 
                posData.PhysicalSizeX
            ])
        else:
            spacing = np.array([
                posData.PhysicalSizeY, 
                posData.PhysicalSizeX
            ])
        velocities_pxl, velocities_um = core.compute_twoframes_velocity(
            prev_lab, lab, spacing=spacing
        )
        acdc_df['velocity_pixel'] = velocities_pxl
        acdc_df['velocity_um'] = velocities_um
        return acdc_df

    def addVolumeMetrics(self, df, rp, posData):
        PhysicalSizeY = posData.PhysicalSizeY
        PhysicalSizeX = posData.PhysicalSizeX
        yx_pxl_to_um2 = PhysicalSizeY*PhysicalSizeX
        vox_to_fl_3D = PhysicalSizeY*PhysicalSizeX*posData.PhysicalSizeZ

        init_list = [-2]*len(rp)
        IDs = init_list.copy()
        IDs_vol_vox = init_list.copy()
        IDs_area_pxl = init_list.copy()
        IDs_vol_fl = init_list.copy()
        IDs_area_um2 = init_list.copy()
        if self.mainWin.isSegm3D:
            IDs_vol_vox_3D = init_list.copy()
            IDs_vol_fl_3D = init_list.copy()

        for i, obj in enumerate(rp):
            IDs[i] = obj.label
            IDs_vol_vox[i] = obj.vol_vox
            IDs_vol_fl[i] = obj.vol_fl
            IDs_area_pxl[i] = obj.area
            IDs_area_um2[i] = obj.area*yx_pxl_to_um2
            if self.mainWin.isSegm3D:
                IDs_vol_vox_3D[i] = obj.area
                IDs_vol_fl_3D[i] = obj.area*vox_to_fl_3D

        df['cell_area_pxl'] = pd.Series(data=IDs_area_pxl, index=IDs, dtype=float)
        df['cell_vol_vox'] = pd.Series(data=IDs_vol_vox, index=IDs, dtype=float)
        df['cell_area_um2'] = pd.Series(data=IDs_area_um2, index=IDs, dtype=float)
        df['cell_vol_fl'] = pd.Series(data=IDs_vol_fl, index=IDs, dtype=float)
        if self.mainWin.isSegm3D:
            df['cell_vol_vox_3D'] = pd.Series(data=IDs_vol_vox_3D, index=IDs, dtype=float)
            df['cell_vol_fl_3D'] = pd.Series(data=IDs_vol_fl_3D, index=IDs, dtype=float)

        return df

    def addAdditionalMetadata(self, posData: load.loadData, df: pd.DataFrame):
        for col, val in posData.additionalMetadataValues().items():
            if col in df.columns:
                df.pop(col)
            df.insert(0, col, val)
        
        try:
            df.pop('time_minutes')
        except Exception as e:
            pass
        try:
            df.pop('time_hours')
        except Exception as e:
            pass
        try:
            time_seconds = df.index.get_level_values('time_seconds')
            df.insert(0, 'time_minutes', time_seconds/60)
            df.insert(1, 'time_hours', time_seconds/3600)
        except Exception as e:
            pass

    @workers.worker_exception_handler
    def run(self):
        last_pos = self.mainWin.last_pos
        save_metrics = self.mainWin.save_metrics
        self.time_last_pbar_update = time.perf_counter()
        mode = self.mode
        for p, posData in enumerate(self.mainWin.data[:last_pos]):
            if self.saveWin.aborted:
                self.finished.emit()
                return
            
            # posData.saveSegmHyperparams()
            posData.saveCustomAnnotationParams()
            current_frame_i = posData.frame_i

            if not self.mainWin.isSnapshot:
                last_tracked_i = self.mainWin.last_tracked_i
                if last_tracked_i is None:
                    self.mainWin.saveWin.aborted = True
                    self.finished.emit()
                    return
            elif self.mainWin.isSnapshot:
                last_tracked_i = 0

            if p == 0:
                self.progressBar.emit(0, last_pos*(last_tracked_i+1), 0)

            segm_npz_path = posData.segm_npz_path
            acdc_output_csv_path = posData.acdc_output_csv_path
            last_tracked_i_path = posData.last_tracked_i_path
            end_i = self.mainWin.save_until_frame_i
            if end_i < len(posData.segm_data):
                saved_segm_data = posData.segm_data
            else:
                frame_shape = posData.segm_data.shape[1:]
                segm_shape = (end_i+1, *frame_shape)
                saved_segm_data = np.zeros(segm_shape, dtype=np.uint32)
            npz_delROIs_info = {}
            delROIs_info_path = posData.delROIs_info_path
            acdc_df_li = []
            keys = []

            # Add segmented channel data for calc metrics if requested
            add_user_channel_data = True
            for chName in self.mainWin.chNamesToSkip:
                skipUserChannel = (
                    posData.filename.endswith(chName)
                    or posData.filename.endswith(f'{chName}_aligned')
                )
                if skipUserChannel:
                    add_user_channel_data = False

            if add_user_channel_data:
                posData.fluo_data_dict[posData.filename] = posData.img_data

            posData.fluo_bkgrData_dict[posData.filename] = posData.bkgrData

            posData.setLoadedChannelNames()
            self.mainWin.initMetricsToSave(posData)

            self.progress.emit(f'Saving {posData.relPath}')
            for frame_i, data_dict in enumerate(posData.allData_li[:end_i+1]):
                if self.saveWin.aborted:
                    self.finished.emit()
                    return

                # Build saved_segm_data
                lab = data_dict['labels']
                if lab is None:
                    break
                
                posData.lab = lab

                if posData.SizeT > 1:
                    saved_segm_data[frame_i] = lab
                else:
                    saved_segm_data = lab

                acdc_df = data_dict['acdc_df']

                if self.saveOnlySegm:
                    continue

                if acdc_df is None:
                    continue

                if not np.any(lab):
                    continue

                # Build acdc_df and index it in each frame_i of acdc_df_li
                try:
                    acdc_df = load.pd_bool_to_int(acdc_df, inplace=False)
                    rp = data_dict['regionprops']
                    if save_metrics:
                        if frame_i > 0:
                            prev_data_dict = posData.allData_li[frame_i-1]
                            prev_lab = prev_data_dict['labels']
                            acdc_df = self.addVelocityMeasurement(
                                acdc_df, prev_lab, lab, posData
                            )
                        acdc_df = self.addMetrics_acdc_df(
                            acdc_df, rp, frame_i, lab, posData
                        )
                        if self.abort:
                            self.progress.emit(f'Saving process aborted.')
                            self.finished.emit()
                            return
                    elif mode == 'Cell cycle analysis':
                        acdc_df = self.addVolumeMetrics(
                            acdc_df, rp, posData
                        )
                    acdc_df_li.append(acdc_df)
                    key = (frame_i, posData.TimeIncrement*frame_i)
                    keys.append(key)
                except Exception as error:
                    self.addMetricsCritical.emit(
                        traceback.format_exc(), str(error)
                    )
                
                try:
                    if save_metrics and frame_i > 0:
                        prev_data_dict = posData.allData_li[frame_i-1]
                        prev_lab = prev_data_dict['labels']
                        acdc_df = self.addVelocityMeasurement(
                            acdc_df, prev_lab, lab, posData
                        )
                except Exception as error:
                    self.addMetricsCritical.emit(
                        traceback.format_exc(), str(error)
                    )

                t = time.perf_counter()
                exec_time = t - self.time_last_pbar_update
                self.progressBar.emit(1, -1, exec_time)
                self.time_last_pbar_update = t

            # Save segmentation file
            np.savez_compressed(segm_npz_path, np.squeeze(saved_segm_data))
            posData.segm_data = saved_segm_data
            try:
                os.remove(posData.segm_npz_temp_path)
            except Exception as e:
                pass

            if posData.segmInfo_df is not None:
                try:
                    posData.segmInfo_df.to_csv(posData.segmInfo_df_csv_path)
                except PermissionError:
                    err_msg = (
                        'The below file is open in another app '
                        '(Excel maybe?).\n\n'
                        f'{posData.segmInfo_df_csv_path}\n\n'
                        'Close file and then press "Ok".'
                    )
                    self.mutex.lock()
                    self.criticalPermissionError.emit(err_msg)
                    self.waitCond.wait(self.mutex)
                    self.mutex.unlock()
                    posData.segmInfo_df.to_csv(posData.segmInfo_df_csv_path)

            if self.saveOnlySegm:
                # Go back to current frame
                posData.frame_i = current_frame_i
                self.mainWin.get_data()
                continue

            if add_user_channel_data:
                posData.fluo_data_dict.pop(posData.filename)

            posData.fluo_bkgrData_dict.pop(posData.filename)

            if posData.SizeT > 1:
                self.progress.emit('Almost done...')
                self.progressBar.emit(0, 0, 0)

            if acdc_df_li:
                all_frames_acdc_df = pd.concat(
                    acdc_df_li, keys=keys,
                    names=['frame_i', 'time_seconds', 'Cell_ID']
                )
                if save_metrics:
                    self.addCombineMetrics_acdc_df(
                        posData, all_frames_acdc_df
                    )

                self.addAdditionalMetadata(posData, all_frames_acdc_df)

                all_frames_acdc_df = self._removeDeprecatedRows(
                    all_frames_acdc_df
                )
                try:
                    # Save segmentation metadata
                    load.store_copy_acdc_df(
                        posData, acdc_output_csv_path, 
                        log_func=self.progress.emit
                    )
                    all_frames_acdc_df.to_csv(acdc_output_csv_path)
                    posData.acdc_df = all_frames_acdc_df
                    try:
                        os.remove(posData.acdc_output_temp_csv_path)
                    except Exception as e:
                        pass
                except PermissionError:
                    err_msg = (
                        'The below file is open in another app '
                        '(Excel maybe?).\n\n'
                        f'{acdc_output_csv_path}\n\n'
                        'Close file and then press "Ok".'
                    )
                    self.mutex.lock()
                    self.criticalPermissionError.emit(err_msg)
                    self.waitCond.wait(self.mutex)
                    self.mutex.unlock()

                    # Save segmentation metadata
                    all_frames_acdc_df.to_csv(acdc_output_csv_path)
                    posData.acdc_df = all_frames_acdc_df
                except Exception as e:
                    self.mutex.lock()
                    self.critical.emit(traceback.format_exc())
                    self.waitCond.wait(self.mutex)
                    self.mutex.unlock()

            with open(last_tracked_i_path, 'w+') as txt:
                txt.write(str(frame_i))

            # Save combined metrics equations
            posData.saveCombineMetrics()
            self.mainWin.pointsLayerDataToDf(posData)
            posData.saveClickEntryPointsDfs()

            posData.last_tracked_i = last_tracked_i

            # Go back to current frame
            posData.frame_i = current_frame_i
            self.mainWin.get_data()

            if mode == 'Segmentation and Tracking' or mode == 'Viewer':
                self.progress.emit(
                    f'Saved data until frame number {frame_i+1}'
                )
            elif mode == 'Cell cycle analysis':
                self.progress.emit(
                    'Saved cell cycle annotations until frame '
                    f'number {last_tracked_i+1}'
                )
            # self.progressBar.emit(1)
        if self.mainWin.isSnapshot:
            self.progress.emit(f'Saved all {p+1} Positions!')
        
        self.finished.emit()
        
        
class guiWin(QMainWindow):
    """Main Window."""

    sigClosed = Signal(object)

    def __init__(
            self, app, parent=None, buttonToRestore=None,
            mainWin=None, version=None
        ):
        """Initializer."""

        super().__init__(parent)

        self._version = version

        from .trackers.YeaZ import tracking as tracking_yeaz
        self.tracking_yeaz = tracking_yeaz

        from .config import parser_args
        self.debug = parser_args['debug']

        self.buttonToRestore = buttonToRestore
        self.mainWin = mainWin
        self.app = app
        self.closeGUI = False

        self.setAcceptDrops(True)
        self._appName = 'Cell-ACDC'
    
    def _printl(
            self, *objects, is_decorator=False, **kwargs
        ):
        timestap = datetime.datetime.now().strftime('%H:%M:%S')
        currentframe = inspect.currentframe()
        outerframes = inspect.getouterframes(currentframe)
        idx = 2 if is_decorator else 1
        callingframe = outerframes[idx].frame
        callingframe_info = inspect.getframeinfo(callingframe)
        filpath = callingframe_info.filename
        filename = os.path.basename(filpath)
        self.logger.info('*'*30)
        self.logger.info(
            f'{timestap} - File "{filename}", line {callingframe_info.lineno}:'
        )
        if kwargs.get('pretty'):
            txt = pprint.pformat(objects[0])
        else:
            txt = ', '.join([str(x) for x in objects])
        self.logger.info(txt)
        self.logger.info('='*30)
    
    def _print(self, *objects):
        self.logger.info(', '.join([str(x) for x in objects]))
            
    def run(self, module='acdc_gui', logs_path=None):
        global print, printl
        
        self.is_win = sys.platform.startswith("win")
        if self.is_win:
            self.openFolderText = 'Show in Explorer...'
        else:
            self.openFolderText = 'Reveal in Finder...'

        self.is_error_state = False
        logger, logs_path, log_path, log_filename = setupLogger(
            module=module, logs_path=logs_path
        )
        if self._version is not None:
            logger.info(f'Initializing GUI v{self._version}')
        else:
            logger.info(f'Initializing GUI...')
        self.logger = logger
        self.log_path = log_path
        self.log_filename = log_filename
        self.logs_path = logs_path

        # print = self._print
        printl = self._printl

        self.initProfileModels()
        self.loadLastSessionSettings()

        self.progressWin = None
        self.slideshowWin = None
        self.ccaTableWin = None
        self.customAnnotButton = None
        self.dataIsLoaded = False
        self.highlightedID = 0
        self.hoverLabelID = 0
        self.expandingID = -1
        self.count = 0
        self.isDilation = True
        self.flag = True
        self.currentPropsID = 0
        self.isSegm3D = False
        self.newSegmEndName = ''
        self.closeGUI = False
        self.img1ChannelGradients = {}
        self.filtersWins = {}
        self.AutoPilotProfile = autopilot.AutoPilotProfile()
        self.storeStateWorker = None
        self.AutoPilot = None
        self.widgetsWithShortcut = {}

        self.setWindowTitle("Cell-ACDC - GUI")
        self.setWindowIcon(QIcon(":icon.ico"))

        self.checkableButtons = []
        self.LeftClickButtons = []
        self.customAnnotDict = {}

        # Keep a list of functions that are not functional in 3D, yet
        self.functionsNotTested3D = []

        self.isSnapshot = False
        self.debugFlag = False
        self.pos_i = 0
        self.save_until_frame_i = 0
        self.countKeyPress = 0
        self.xHoverImg, self.yHoverImg = None, None

        # Buttons added to QButtonGroup will be mutually exclusive
        self.checkableQButtonsGroup = QButtonGroup(self)
        self.checkableQButtonsGroup.setExclusive(False)

        self.lazyLoader = None

        self.gui_createCursors()
        self.gui_createActions()
        self.gui_createMenuBar()
        self.gui_createToolBars()
        self.gui_createControlsToolbar()
        self.gui_createShowPropsButton()
        self.gui_createRegionPropsDockWidget()
        self.gui_createQuickSettingsWidgets()

        self.autoSaveGarbageWorkers = []
        self.autoSaveActiveWorkers = []

        self.gui_connectActions()
        self.gui_createStatusBar()
        self.gui_createTerminalWidget()

        self.gui_createGraphicsPlots()
        self.gui_addGraphicsItems()

        self.gui_createImg1Widgets()
        self.gui_createLabWidgets()
        self.gui_createBottomWidgetsToBottomLayout()

        mainContainer = QWidget()
        self.setCentralWidget(mainContainer)

        mainLayout = self.gui_createMainLayout()
        self.mainLayout = mainLayout

        mainContainer.setLayout(mainLayout)

        self.isEditActionsConnected = False

        self.readRecentPaths()

        self.initShortcuts()
        self.show()
        # self.installEventFilter(self)

        self.logger.info('GUI ready.')
    
    def initProfileModels(self):
        self.logger.info('Initiliazing profilers...')
        
        from ._profile.spline_to_obj import model
        
        self.splineToObjModel = model.Model()

        self.splineToObjModel.fit()
    
    def readRecentPaths(self, recent_paths_path=None):
        # Step 0. Remove the old options from the menu
        self.openRecentMenu.clear()

        # Step 1. Read recent Paths
        if recent_paths_path is None:
            recent_paths_path = recentPaths_path    
        
        if os.path.exists(recent_paths_path):
            df = pd.read_csv(recent_paths_path, index_col='index')
            if 'opened_last_on' in df.columns:
                df = df.sort_values('opened_last_on', ascending=False)
            recentPaths = df['path'].to_list()
        else:
            recentPaths = []
        
        # Step 2. Dynamically create the actions
        actions = []
        for path in recentPaths:
            if not os.path.exists(path):
                continue
            action = QAction(path, self)
            action.triggered.connect(partial(self.openRecentFile, path))
            actions.append(action)

        # Step 3. Add the actions to the menu
        self.openRecentMenu.addActions(actions)
    
    def addPathToOpenRecentMenu(self, path):
        for action in self.openRecentMenu.actions():
            if path == action.text():
                break
        else:
            action = QAction(path, self)
            action.triggered.connect(partial(self.openRecentFile, path))
        
        try:
            firstAction = self.openRecentMenu.actions()[0]
            self.openRecentMenu.insertAction(firstAction, action)
        except Exception as e:
            pass

    def loadLastSessionSettings(self):
        self.settings_csv_path = settings_csv_path
        if os.path.exists(settings_csv_path):
            self.df_settings = pd.read_csv(
                settings_csv_path, index_col='setting'
            )
            if 'is_bw_inverted' not in self.df_settings.index:
                self.df_settings.at['is_bw_inverted', 'value'] = 'No'
            else:
                self.df_settings.loc['is_bw_inverted'] = (
                    self.df_settings.loc['is_bw_inverted'].astype(str)
                )
            if 'fontSize' not in self.df_settings.index:
                self.df_settings.at['fontSize', 'value'] = 12
            if 'overlayColor' not in self.df_settings.index:
                self.df_settings.at['overlayColor', 'value'] = '255-255-0'
            if 'how_normIntensities' not in self.df_settings.index:
                raw = 'Do not normalize. Display raw image'
                self.df_settings.at['how_normIntensities', 'value'] = raw
        else:
            idx = ['is_bw_inverted', 'fontSize', 'overlayColor', 'how_normIntensities']
            values = ['No', 12, '255-255-0', 'raw']
            self.df_settings = pd.DataFrame({
                'setting': idx,'value': values}
            ).set_index('setting')
        
        if 'isLabelsVisible' not in self.df_settings.index:
            self.df_settings.at['isLabelsVisible', 'value'] = 'No'
        
        if 'isRightImageVisible' not in self.df_settings.index:
            self.df_settings.at['isRightImageVisible', 'value'] = 'Yes'
        
        if 'manual_separate_draw_mode' not in self.df_settings.index:
            col = 'manual_separate_draw_mode'
            self.df_settings.at[col, 'value'] = 'threepoints_arc'
        
        if 'colorScheme' in self.df_settings.index:
            col = 'colorScheme'
            self._colorScheme = self.df_settings.at[col, 'value']
        else:
            self._colorScheme = 'light'

    def dragEnterEvent(self, event):
        file_path = event.mimeData().urls()[0].toLocalFile()
        if os.path.isdir(file_path):
            exp_path = file_path
            basename = os.path.basename(file_path)
            if basename.find('Position_')!=-1 or basename=='Images':
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.acceptProposedAction()

    def dropEvent(self, event):
        event.setDropAction(Qt.CopyAction)
        file_path = event.mimeData().urls()[0].toLocalFile()
        self.logger.info(f'Dragged and dropped path "{file_path}"')
        basename = os.path.basename(file_path)
        if os.path.isdir(file_path):
            exp_path = file_path
            self.openFolder(exp_path=exp_path)
        else:
            self.openFile(file_path=file_path)

    def leaveEvent(self, event):
        if self.slideshowWin is not None:
            posData = self.data[self.pos_i]
            mainWinGeometry = self.geometry()
            mainWinLeft = mainWinGeometry.left()
            mainWinTop = mainWinGeometry.top()
            mainWinWidth = mainWinGeometry.width()
            mainWinHeight = mainWinGeometry.height()
            mainWinRight = mainWinLeft+mainWinWidth
            mainWinBottom = mainWinTop+mainWinHeight

            slideshowWinGeometry = self.slideshowWin.geometry()
            slideshowWinLeft = slideshowWinGeometry.left()
            slideshowWinTop = slideshowWinGeometry.top()
            slideshowWinWidth = slideshowWinGeometry.width()
            slideshowWinHeight = slideshowWinGeometry.height()

            # Determine if overlap
            overlap = (
                (slideshowWinTop < mainWinBottom) and
                (slideshowWinLeft < mainWinRight)
            )

            autoActivate = (
                self.dataIsLoaded and not
                overlap and not
                posData.disableAutoActivateViewerWindow
            )

            if autoActivate:
                self.slideshowWin.setFocus()
                self.slideshowWin.activateWindow()

    def enterEvent(self, event):
        event.accept()
        if self.slideshowWin is not None:
            posData = self.data[self.pos_i]
            mainWinGeometry = self.geometry()
            mainWinLeft = mainWinGeometry.left()
            mainWinTop = mainWinGeometry.top()
            mainWinWidth = mainWinGeometry.width()
            mainWinHeight = mainWinGeometry.height()
            mainWinRight = mainWinLeft+mainWinWidth
            mainWinBottom = mainWinTop+mainWinHeight

            slideshowWinGeometry = self.slideshowWin.geometry()
            slideshowWinLeft = slideshowWinGeometry.left()
            slideshowWinTop = slideshowWinGeometry.top()
            slideshowWinWidth = slideshowWinGeometry.width()
            slideshowWinHeight = slideshowWinGeometry.height()

            # Determine if overlap
            overlap = (
                (slideshowWinTop < mainWinBottom) and
                (slideshowWinLeft < mainWinRight)
            )

            autoActivate = (
                self.dataIsLoaded and not
                overlap and not
                posData.disableAutoActivateViewerWindow
            )

            if autoActivate:
                self.setFocus()
                self.activateWindow()

    def isPanImageClick(self, mouseEvent, modifiers):
        left_click = mouseEvent.button() == Qt.MouseButton.LeftButton
        return modifiers == Qt.AltModifier and left_click

    def isMiddleClick(self, mouseEvent, modifiers):
        if sys.platform == 'darwin':
            middle_click = (
                mouseEvent.button() == Qt.MouseButton.LeftButton
                and modifiers == Qt.ControlModifier
                and not self.brushButton.isChecked()
            )
        else:
            middle_click = mouseEvent.button() == Qt.MouseButton.MiddleButton
        return middle_click

    def gui_createCursors(self):
        pixmap = QPixmap(":wand_cursor.svg")
        self.wandCursor = QCursor(pixmap, 16, 16)

        pixmap = QPixmap(":curv_cursor.svg")
        self.curvCursor = QCursor(pixmap, 16, 16)

        pixmap = QPixmap(":addDelPolyLineRoi_cursor.svg")
        self.polyLineRoiCursor = QCursor(pixmap, 16, 16)
        
        pixmap = QPixmap(":cross_cursor.svg")
        self.addPointsCursor = QCursor(pixmap, 16, 16)

    def gui_createMenuBar(self):
        menuBar = self.menuBar()

        # File menu
        fileMenu = QMenu("&File", self)
        self.fileMenu = fileMenu
        menuBar.addMenu(fileMenu)
        fileMenu.addAction(self.newAction)
        fileMenu.addAction(self.openAction)
        fileMenu.addAction(self.openFileAction)
        # Open Recent submenu
        self.openRecentMenu = fileMenu.addMenu("Open Recent")
        fileMenu.addAction(self.manageVersionsAction)
        fileMenu.addAction(self.saveAction)
        fileMenu.addAction(self.saveAsAction)
        fileMenu.addAction(self.quickSaveAction)
        fileMenu.addAction(self.loadFluoAction)
        fileMenu.addAction(self.loadPosAction)
        # Separator
        self.fileMenu.lastSeparator = fileMenu.addSeparator()
        fileMenu.addAction(self.exitAction)
        
        # Edit menu
        editMenu = menuBar.addMenu("&Edit")
        editMenu.addSeparator()

        editMenu.addAction(self.editShortcutsAction)
        editMenu.addAction(self.editTextIDsColorAction)
        editMenu.addAction(self.editOverlayColorAction)
        editMenu.addAction(self.manuallyEditCcaAction)
        editMenu.addAction(self.enableSmartTrackAction)
        editMenu.addAction(self.enableAutoZoomToCellsAction)

        # View menu
        self.viewMenu = menuBar.addMenu("&View")
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.viewCcaTableAction)

        # Image menu
        ImageMenu = menuBar.addMenu("&Image")
        ImageMenu.addSeparator()
        ImageMenu.addAction(self.imgPropertiesAction)
        filtersMenu = ImageMenu.addMenu("Filters")
        for filtersDict in self.filtersWins.values():
            filtersMenu.addAction(filtersDict['action'])
        
        normalizeIntensitiesMenu = ImageMenu.addMenu("Normalize intensities")
        normalizeIntensitiesMenu.addAction(self.normalizeRawAction)
        normalizeIntensitiesMenu.addAction(self.normalizeToFloatAction)
        # normalizeIntensitiesMenu.addAction(self.normalizeToUbyteAction)
        normalizeIntensitiesMenu.addAction(self.normalizeRescale0to1Action)
        normalizeIntensitiesMenu.addAction(self.normalizeByMaxAction)
        ImageMenu.addAction(self.invertBwAction)
        ImageMenu.addAction(self.saveLabColormapAction)
        ImageMenu.addAction(self.shuffleCmapAction)
        ImageMenu.addAction(self.greedyShuffleCmapAction)
        ImageMenu.addAction(self.zoomToObjsAction)
        ImageMenu.addAction(self.zoomOutAction)

        # Segment menu
        SegmMenu = menuBar.addMenu("&Segment")
        SegmMenu.addSeparator()
        self.segmSingleFrameMenu = SegmMenu.addMenu('Segment displayed frame')
        for action in self.segmActions:
            self.segmSingleFrameMenu.addAction(action)

        self.segmSingleFrameMenu.addAction(self.addCustomModelFrameAction)

        self.segmVideoMenu = SegmMenu.addMenu('Segment multiple frames')
        for action in self.segmActionsVideo:
            self.segmVideoMenu.addAction(action)

        self.segmVideoMenu.addAction(self.addCustomModelVideoAction)

        SegmMenu.addAction(self.SegmActionRW)
        SegmMenu.addAction(self.postProcessSegmAction)
        SegmMenu.addAction(self.autoSegmAction)
        SegmMenu.addAction(self.relabelSequentialAction)
        SegmMenu.aboutToShow.connect(self.nonViewerEditMenuOpened)

        # Tracking menu
        trackingMenu = menuBar.addMenu("&Tracking")
        self.trackingMenu = trackingMenu
        trackingMenu.addSeparator()
        selectTrackAlgoMenu = trackingMenu.addMenu(
            'Select real-time tracking algorithm'
        )
        for rtTrackerAction in self.trackingAlgosGroup.actions():
            selectTrackAlgoMenu.addAction(rtTrackerAction)

        trackingMenu.addAction(self.repeatTrackingVideoAction)

        trackingMenu.addAction(self.repeatTrackingMenuAction)
        trackingMenu.aboutToShow.connect(self.nonViewerEditMenuOpened)

        # Measurements menu
        measurementsMenu = menuBar.addMenu("&Measurements")
        self.measurementsMenu = measurementsMenu
        measurementsMenu.addSeparator()
        measurementsMenu.addAction(self.setMeasurementsAction)
        measurementsMenu.addAction(self.addCustomMetricAction)
        measurementsMenu.addAction(self.addCombineMetricAction)
        measurementsMenu.setDisabled(True)

        # Settings menu
        self.settingsMenu = QMenu("Settings", self)
        menuBar.addMenu(self.settingsMenu)
        self.settingsMenu.addAction(self.toggleColorSchemeAction)
        self.settingsMenu.addAction(self.editShortcutsAction)
        self.settingsMenu.addSeparator()

        # Mode menu (actions added when self.modeComboBox is created)
        self.modeMenu = menuBar.addMenu('Mode')
        self.modeMenu.menuAction().setVisible(False)

        # Help menu
        helpMenu = menuBar.addMenu("&Help")
        helpMenu.addAction(self.tipsAction)
        helpMenu.addAction(self.UserManualAction)
        helpMenu.addSeparator()
        helpMenu.addAction(self.aboutAction)

    def gui_createToolBars(self):
        # File toolbar
        fileToolBar = self.addToolBar("File")
        # fileToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        fileToolBar.setMovable(False)

        fileToolBar.addAction(self.newAction)
        fileToolBar.addAction(self.openAction)
        fileToolBar.addAction(self.manageVersionsAction)
        fileToolBar.addAction(self.saveAction)
        fileToolBar.addAction(self.showInExplorerAction)
        # fileToolBar.addAction(self.reloadAction)
        fileToolBar.addAction(self.undoAction)
        fileToolBar.addAction(self.redoAction)
        self.fileToolBar = fileToolBar
        self.setEnabledFileToolbar(False)

        self.undoAction.setEnabled(False)
        self.redoAction.setEnabled(False)

        # Navigation toolbar
        navigateToolBar = QToolBar("Navigation", self)
        navigateToolBar.setContextMenuPolicy(Qt.PreventContextMenu)
        # navigateToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        self.addToolBar(navigateToolBar)
        navigateToolBar.addAction(self.findIdAction)

        self.slideshowButton = QToolButton(self)
        self.slideshowButton.setIcon(QIcon(":eye-plus.svg"))
        self.slideshowButton.setCheckable(True)
        self.slideshowButton.setShortcut('Ctrl+W')
        self.slideshowButton.setToolTip('Open slideshow (Ctrl+W)')
        navigateToolBar.addWidget(self.slideshowButton)

        self.overlayButton = widgets.rightClickToolButton(parent=self)
        self.overlayButton.setIcon(QIcon(":overlay.svg"))
        self.overlayButton.setCheckable(True)
        self.overlayButton.setToolTip(
            'Overlay channels\' images.\n\n'
            'Right-click on the button to overlay additional channels.\n\n'
            'To overlay a different channel right-click on the colorbar on the '
            'left of the image.\n\n'
            'Use the colorbar ticks to adjust the selected channel\'s intensity.\n\n'
            'You can also adjust the opacity of the selected channel with the\n'
            '"Alpha <channel_name>" scrollbar below the image.\n\n'
            'NOTE: This button has a green background if you successfully '
            'loaded fluorescence data'
        )
        self.overlayButtonAction = navigateToolBar.addWidget(self.overlayButton)
        # self.checkableButtons.append(self.overlayButton)
        # self.checkableQButtonsGroup.addButton(self.overlayButton)

        self.addPointsLayerAction = QAction('Add points layer', self)
        self.addPointsLayerAction.setIcon(QIcon(":addPointsLayer.svg"))
        self.addPointsLayerAction.setToolTip(
            'Add points layer as a scatter plot'
        )
        navigateToolBar.addAction(self.addPointsLayerAction)

        self.overlayLabelsButton = widgets.rightClickToolButton(parent=self)
        self.overlayLabelsButton.setIcon(QIcon(":overlay_labels.svg"))
        self.overlayLabelsButton.setCheckable(True)
        self.overlayLabelsButton.setToolTip(
            'Add contours layer from another segmentation file'
        )
        # self.overlayLabelsButton.setVisible(False)
        self.overlayLabelsButtonAction = navigateToolBar.addWidget(
            self.overlayLabelsButton
        )
        self.overlayLabelsButtonAction.setVisible(False)

        self.rulerButton = QToolButton(self)
        self.rulerButton.setIcon(QIcon(":ruler.svg"))
        self.rulerButton.setCheckable(True)
        self.rulerButton.setToolTip(
            'Draw a straight line and show its length. '
            'Length is displayed on the bottom-right corner.'
        )
        navigateToolBar.addWidget(self.rulerButton)
        self.checkableButtons.append(self.rulerButton)
        self.LeftClickButtons.append(self.rulerButton)

        # fluorescence image color widget
        colorsToolBar = QToolBar("Colors", self)

        self.overlayColorButton = pg.ColorButton(self, color=(230,230,230))
        self.overlayColorButton.setDisabled(True)
        colorsToolBar.addWidget(self.overlayColorButton)

        self.textIDsColorButton = pg.ColorButton(self)
        colorsToolBar.addWidget(self.textIDsColorButton)

        self.addToolBar(colorsToolBar)
        colorsToolBar.setVisible(False)

        self.navigateToolBar = navigateToolBar

        # cca toolbar
        ccaToolBar = QToolBar("Cell cycle annotations", self)
        self.addToolBar(ccaToolBar)

        # Assign mother to bud button
        self.assignBudMothButton = QToolButton(self)
        self.assignBudMothButton.setIcon(QIcon(":assign-motherbud.svg"))
        self.assignBudMothButton.setCheckable(True)
        self.assignBudMothButton.setShortcut('a')
        self.assignBudMothButton.setVisible(False)
        self.assignBudMothButton.setToolTip(
            'Toggle "Assign bud to mother cell" mode ON/OFF\n\n'
            'ACTION: press with right button on bud and release on mother '
            '(right-click drag-and-drop)\n\n'
            'SHORTCUT: "A" key'
        )
        self.assignBudMothButton.action = ccaToolBar.addWidget(self.assignBudMothButton)
        self.checkableButtons.append(self.assignBudMothButton)
        self.checkableQButtonsGroup.addButton(self.assignBudMothButton)
        self.functionsNotTested3D.append(self.assignBudMothButton)
        

        # Set is_history_known button
        self.setIsHistoryKnownButton = QToolButton(self)
        self.setIsHistoryKnownButton.setIcon(QIcon(":history.svg"))
        self.setIsHistoryKnownButton.setCheckable(True)
        self.setIsHistoryKnownButton.setShortcut('u')
        self.setIsHistoryKnownButton.setVisible(False)
        self.setIsHistoryKnownButton.setToolTip(
            'Toggle "Annotate unknown history" mode ON/OFF\n\n'
            'EXAMPLE: useful for cells appearing from outside of the field of view\n\n'
            'ACTION: Right-click on cell\n\n'
            'SHORTCUT: "U" key'
        )
        self.setIsHistoryKnownButton.action = ccaToolBar.addWidget(self.setIsHistoryKnownButton)
        self.checkableButtons.append(self.setIsHistoryKnownButton)
        self.checkableQButtonsGroup.addButton(self.setIsHistoryKnownButton)
        self.functionsNotTested3D.append(self.setIsHistoryKnownButton)
        

        ccaToolBar.addAction(self.assignBudMothAutoAction)
        ccaToolBar.addAction(self.editCcaToolAction)
        ccaToolBar.addAction(self.reInitCcaAction)
        ccaToolBar.setVisible(False)
        self.ccaToolBar = ccaToolBar
        self.functionsNotTested3D.append(self.assignBudMothAutoAction)
        self.functionsNotTested3D.append(self.reInitCcaAction)
        self.functionsNotTested3D.append(self.editCcaToolAction)

        # Edit toolbar
        editToolBar = QToolBar("Edit", self)
        editToolBar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(editToolBar)

        self.brushButton = QToolButton(self)
        self.brushButton.setIcon(QIcon(":brush.svg"))
        self.brushButton.setCheckable(True)
        self.brushButton.setToolTip(
            'Edit segmentation labels with a circular brush.\n'
            'Increase brush size with UP/DOWN arrows on the keyboard.\n\n'
            'Default behaviour:\n'
            '   - Painting on the background will create a new label.\n'
            '   - Edit an existing label by starting to paint on the label\n'
            '     (brush cursor changes color when hovering an existing label).\n'
            '   - Press `Shift` to force drawing a new object\n'
            '   - Painting in default mode always draws UNDER existing labels.\n\n'
            'Power brush mode:\n'
            '   - Power brush: press "b" key twice quickly to force the brush\n'
            '     to draw ABOVE existing labels.\n'
            '     NOTE: If double-press is successful, then brush button turns red.\n'
            '     and brush cursor always white.\n'
            '   - Power brush will draw a new object unless you keep "Ctrl" pressed.\n'
            '     --> draw the ID you start the painting from.'
            'Manual ID mode:\n'
            '   - Toggle the manual ID mode with the "Auto-ID" checkbox on the\n'
            '     top-right toolbar.\n'
            '   - Enter the ID that you want to paint.\n'
            '     NOTE: use the power brush to draw ABOVE the existing labels.\n\n'
            'SHORTCUT: "B" key'
        )
        editToolBar.addWidget(self.brushButton)
        self.checkableButtons.append(self.brushButton)
        self.LeftClickButtons.append(self.brushButton)
        self.brushButton.keyPressShortcut = Qt.Key_B
        self.widgetsWithShortcut['Brush'] = self.brushButton

        self.eraserButton = QToolButton(self)
        self.eraserButton.setIcon(QIcon(":eraser.svg"))
        self.eraserButton.setCheckable(True)
        self.eraserButton.setToolTip(
            'Erase segmentation labels with a circular eraser.\n'
            'Increase eraser size with UP/DOWN arrows on the keyboard.\n\n'
            'Default behaviour:\n\n'
            '   - Starting to erase from the background (cursor is a red circle)\n '
            '     will erase any labels you hover above.\n'
            '   - Starting to erase from a specific label will erase only that label\n'
            '     (cursor is a circle with the color of the label).\n'
            '   - To enforce erasing all labels no matter where you start from\n'
            '     double-press "X" key. If double-press is successfull,\n'
            '     then eraser button is red and eraser cursor always red.\n\n'
            'SHORTCUT: "X" key')
        editToolBar.addWidget(self.eraserButton)
        self.eraserButton.keyPressShortcut = Qt.Key_X
        self.widgetsWithShortcut['Eraser'] = self.eraserButton
        self.checkableButtons.append(self.eraserButton)
        self.LeftClickButtons.append(self.eraserButton)

        self.curvToolButton = QToolButton(self)
        self.curvToolButton.setIcon(QIcon(":curvature-tool.svg"))
        self.curvToolButton.setCheckable(True)
        self.curvToolButton.setShortcut('c')
        self.curvToolButton.setToolTip(
            'Toggle "Curvature tool" ON/OFF\n\n'
            'ACTION: left-clicks for manual spline anchors,\n'
            'right button for drawing auto-contour\n\n'
            'SHORTCUT: "C" key')
        self.curvToolButton.action = editToolBar.addWidget(self.curvToolButton)
        self.LeftClickButtons.append(self.curvToolButton)
        self.functionsNotTested3D.append(self.curvToolButton)
        self.widgetsWithShortcut['Curvature tool'] = self.curvToolButton
        # self.checkableButtons.append(self.curvToolButton)

        self.wandToolButton = QToolButton(self)
        self.wandToolButton.setIcon(QIcon(":magic_wand.svg"))
        self.wandToolButton.setCheckable(True)
        self.wandToolButton.setShortcut('w')
        self.wandToolButton.setToolTip(
            'Toggle "Magic wand tool" ON/OFF\n\n'
            'ACTION: left-click for single selection,\n'
            'or left-click and then drag for continous selection\n\n'
            'SHORTCUT: "W" key')
        self.wandToolButton.action = editToolBar.addWidget(self.wandToolButton)
        self.LeftClickButtons.append(self.wandToolButton)
        self.functionsNotTested3D.append(self.wandToolButton)
        self.widgetsWithShortcut['Magic wand'] = self.wandToolButton

        self.widgetsWithShortcut['Annotate mother/daughter pairing'] = (
            self.assignBudMothButton
        )
        self.widgetsWithShortcut['Annotate unknown history'] = (
            self.setIsHistoryKnownButton
        )

        self.labelRoiButton = widgets.rightClickToolButton(parent=self)
        self.labelRoiButton.setIcon(QIcon(":label_roi.svg"))
        self.labelRoiButton.setCheckable(True)
        self.labelRoiButton.setShortcut('l')
        self.labelRoiButton.setToolTip(
            'Toggle "Magic labeller" ON/OFF\n\n'
            'ACTION: Draw a rectangular ROI aroung object(s) you want to segment\n\n'
            'Draw with LEFT button to label with last used model\n'
            'Draw with RIGHT button to choose a different segmentation model\n\n'
            'SHORTCUT: "L" key')
        self.labelRoiButton.action = editToolBar.addWidget(self.labelRoiButton)
        self.LeftClickButtons.append(self.labelRoiButton)
        self.checkableButtons.append(self.labelRoiButton)
        self.checkableQButtonsGroup.addButton(self.labelRoiButton)
        self.widgetsWithShortcut['Label ROI'] = self.labelRoiButton
        # self.functionsNotTested3D.append(self.labelRoiButton)

        self.segmentToolAction = QAction('Segment with last used model', self)
        self.segmentToolAction.setIcon(QIcon(":segment.svg"))
        self.segmentToolAction.setShortcut('r')
        self.segmentToolAction.setToolTip(
            'Segment with last used model and last used parameters.\n\n'
            'If you never selected a segmentation model before, you will be \n'
            'asked to choose it and initialize its parameters.\n\n'
            'SHORTCUT: "R" key')
        self.widgetsWithShortcut['Repeat segmentation'] = self.segmentToolAction
        editToolBar.addAction(self.segmentToolAction)

        self.hullContToolButton = QToolButton(self)
        self.hullContToolButton.setIcon(QIcon(":hull.svg"))
        self.hullContToolButton.setCheckable(True)
        self.hullContToolButton.setShortcut('o')
        self.hullContToolButton.setToolTip(
            'Toggle "Hull contour" ON/OFF\n\n'
            'ACTION: right-click on a cell to replace it with its hull contour.\n'
            'Use it to fill cracks and holes.\n\n'
            'SHORTCUT: "K" key')
        self.hullContToolButton.action = editToolBar.addWidget(self.hullContToolButton)
        self.checkableButtons.append(self.hullContToolButton)
        self.checkableQButtonsGroup.addButton(self.hullContToolButton)
        self.functionsNotTested3D.append(self.hullContToolButton)
        self.widgetsWithShortcut['Hull contour'] = self.hullContToolButton

        self.fillHolesToolButton = QToolButton(self)
        self.fillHolesToolButton.setIcon(QIcon(":fill_holes.svg"))
        self.fillHolesToolButton.setCheckable(True)
        self.fillHolesToolButton.setShortcut('f')
        self.fillHolesToolButton.setToolTip(
            'Toggle "Fill holes" ON/OFF\n\n'
            'ACTION: right-click on a cell to fill holes\n\n'
            'SHORTCUT: "F" key')
        self.fillHolesToolButton.action = editToolBar.addWidget(self.fillHolesToolButton)
        self.checkableButtons.append(self.fillHolesToolButton)
        self.checkableQButtonsGroup.addButton(self.fillHolesToolButton)
        self.functionsNotTested3D.append(self.fillHolesToolButton)
        self.widgetsWithShortcut['Fill holes'] = self.fillHolesToolButton

        self.moveLabelToolButton = QToolButton(self)
        self.moveLabelToolButton.setIcon(QIcon(":moveLabel.svg"))
        self.moveLabelToolButton.setCheckable(True)
        self.moveLabelToolButton.setShortcut('p')
        self.moveLabelToolButton.setToolTip(
            'Toggle "Move label (a.k.a. mask)" ON/OFF\n\n'
            'ACTION: right-click drag and drop a labels to move it around\n\n'
            'SHORTCUT: "P" key')
        self.moveLabelToolButton.action = editToolBar.addWidget(self.moveLabelToolButton)
        self.checkableButtons.append(self.moveLabelToolButton)
        self.checkableQButtonsGroup.addButton(self.moveLabelToolButton)
        self.widgetsWithShortcut['Move label'] = self.moveLabelToolButton

        self.expandLabelToolButton = QToolButton(self)
        self.expandLabelToolButton.setIcon(QIcon(":expandLabel.svg"))
        self.expandLabelToolButton.setCheckable(True)
        self.expandLabelToolButton.setShortcut('e')
        self.expandLabelToolButton.setToolTip(
            'Toggle "Expand/Shrink label (a.k.a. masks)" ON/OFF\n\n'
            'ACTION: leave mouse cursor on the label you want to expand/shrink'
            'and press arrow up/down on the keyboard to expand/shrink the mask.\n\n'
            'SHORTCUT: "E" key')
        self.expandLabelToolButton.action = editToolBar.addWidget(self.expandLabelToolButton)
        self.expandLabelToolButton.hide()
        self.checkableButtons.append(self.expandLabelToolButton)
        self.LeftClickButtons.append(self.expandLabelToolButton)
        self.checkableQButtonsGroup.addButton(self.expandLabelToolButton)
        self.widgetsWithShortcut['Expand/shrink label'] = self.expandLabelToolButton

        self.editIDbutton = QToolButton(self)
        self.editIDbutton.setIcon(QIcon(":edit-id.svg"))
        self.editIDbutton.setCheckable(True)
        self.editIDbutton.setShortcut('n')
        self.editIDbutton.setToolTip(
            'Toggle "Edit ID" mode ON/OFF\n\n'
            'EXAMPLE: manually change ID of a cell\n\n'
            'ACTION: right-click on cell\n\n'
            'SHORTCUT: "N" key')
        editToolBar.addWidget(self.editIDbutton)
        self.checkableButtons.append(self.editIDbutton)
        self.checkableQButtonsGroup.addButton(self.editIDbutton)
        self.widgetsWithShortcut['Edit ID'] = self.editIDbutton

        self.separateBudButton = QToolButton(self)
        self.separateBudButton.setIcon(QIcon(":separate-bud.svg"))
        self.separateBudButton.setCheckable(True)
        self.separateBudButton.setShortcut('s')
        self.separateBudButton.setToolTip(
            'Toggle "Automatic/manual separation" mode ON/OFF\n\n'
            'EXAMPLE: separate mother-bud fused together\n\n'
            'ACTION: right-click for automatic and Ctrl+right-click for manual\n\n'
            'SHORTCUT: "S" key'
        )
        self.separateBudButton.action = editToolBar.addWidget(self.separateBudButton)
        self.checkableButtons.append(self.separateBudButton)
        self.checkableQButtonsGroup.addButton(self.separateBudButton)
        self.functionsNotTested3D.append(self.separateBudButton)
        self.widgetsWithShortcut['Separate objects'] = self.separateBudButton

        self.mergeIDsButton = QToolButton(self)
        self.mergeIDsButton.setIcon(QIcon(":merge-IDs.svg"))
        self.mergeIDsButton.setCheckable(True)
        self.mergeIDsButton.setShortcut('m')
        self.mergeIDsButton.setToolTip(
            'Toggle "Merge IDs" mode ON/OFF\n\n'
            'EXAMPLE: merge/fuse two cells together\n\n'
            'ACTION: right-click\n\n'
            'SHORTCUT: "M" key'
        )
        self.mergeIDsButton.action = editToolBar.addWidget(self.mergeIDsButton)
        self.checkableButtons.append(self.mergeIDsButton)
        self.checkableQButtonsGroup.addButton(self.mergeIDsButton)
        self.functionsNotTested3D.append(self.mergeIDsButton)
        self.widgetsWithShortcut['Merge objects'] = self.mergeIDsButton

        self.keepIDsButton = QToolButton(self)
        self.keepIDsButton.setIcon(QIcon(":keep_objects.svg"))
        self.keepIDsButton.setCheckable(True)
        self.keepIDsButton.setToolTip(
            'Toggle "Select objects to keep" mode ON/OFF\n\n'
            'EXAMPLE: Select the objects to keep. Press "Enter" to confirm '
            'selection or "Esc" to clear the selection.\n'
            'After confirming, all the NON selected objects will be deleted.\n\n'
            'ACTION: right- or left-click on objects to keep\n\n'
        )
        self.keepIDsButton.action = editToolBar.addWidget(self.keepIDsButton)
        self.keepIDsButton.setShortcut('k')
        self.checkableButtons.append(self.keepIDsButton)
        self.checkableQButtonsGroup.addButton(self.keepIDsButton)
        # self.functionsNotTested3D.append(self.keepIDsButton)
        self.widgetsWithShortcut['Select objects to keep'] = self.keepIDsButton

        self.binCellButton = QToolButton(self)
        self.binCellButton.setIcon(QIcon(":bin.svg"))
        self.binCellButton.setCheckable(True)
        self.binCellButton.setToolTip(
            'Toggle "Annotate cell as removed from analysis" mode ON/OFF\n\n'
            'EXAMPLE: annotate that a cell is removed from downstream analysis.\n'
            '"is_cell_excluded" set to True in acdc_output.csv table\n\n'
            'ACTION: right-click\n\n'
        )
        # self.binCellButton.setShortcut('r')
        self.binCellButton.action = editToolBar.addWidget(self.binCellButton)
        self.checkableButtons.append(self.binCellButton)
        self.checkableQButtonsGroup.addButton(self.binCellButton)
        # self.functionsNotTested3D.append(self.binCellButton)

        self.manualTrackingButton = QToolButton(self)
        self.manualTrackingButton.setIcon(QIcon(":manual_tracking.svg"))
        self.manualTrackingButton.setCheckable(True)
        self.manualTrackingButton.setToolTip(
            'Toggle "Manual tracking" mode ON/OFF\n\n'
            'ACTION: select ID to track and right-click on an object to assign '
            'that ID\n\n'
            'SHORTCUT: "T" key'
        )
        self.manualTrackingButton.setShortcut('T')
        self.checkableQButtonsGroup.addButton(self.manualTrackingButton)
        self.checkableButtons.append(self.manualTrackingButton)
        self.widgetsWithShortcut['Manual tracking'] = self.manualTrackingButton

        self.ripCellButton = QToolButton(self)
        self.ripCellButton.setIcon(QIcon(":rip.svg"))
        self.ripCellButton.setCheckable(True)
        self.ripCellButton.setToolTip(
            'Toggle "Annotate cell as dead" mode ON/OFF\n\n'
            'EXAMPLE: annotate that a cell is dead.\n'
            '"is_cell_dead" set to True in acdc_output.csv table\n\n'
            'ACTION: right-click\n\n'
            'SHORTCUT: "D" key'
        )
        self.ripCellButton.setShortcut('d')
        self.ripCellButton.action = editToolBar.addWidget(self.ripCellButton)
        self.checkableButtons.append(self.ripCellButton)
        self.checkableQButtonsGroup.addButton(self.ripCellButton)
        self.functionsNotTested3D.append(self.ripCellButton)
        self.widgetsWithShortcut['Annotate cell as dead'] = self.ripCellButton

        editToolBar.addAction(self.addDelRoiAction)
        editToolBar.addAction(self.addDelPolyLineRoiAction)
        editToolBar.addAction(self.delBorderObjAction)

        self.addDelRoiAction.toolbar = editToolBar
        self.functionsNotTested3D.append(self.addDelRoiAction)

        self.addDelPolyLineRoiAction.toolbar = editToolBar
        self.functionsNotTested3D.append(self.addDelPolyLineRoiAction)

        self.delBorderObjAction.toolbar = editToolBar
        self.functionsNotTested3D.append(self.delBorderObjAction)

        editToolBar.addAction(self.repeatTrackingAction)
        
        self.manualTrackingAction = editToolBar.addWidget(
            self.manualTrackingButton
        )

        self.functionsNotTested3D.append(self.repeatTrackingAction)
        self.functionsNotTested3D.append(self.manualTrackingAction)

        self.reinitLastSegmFrameAction = QAction(self)
        self.reinitLastSegmFrameAction.setIcon(QIcon(":reinitLastSegm.svg"))
        self.reinitLastSegmFrameAction.setVisible(False)
        self.reinitLastSegmFrameAction.setToolTip(
            'Reset last segmented frame to current one.\n'
            'NOTE: This will re-enable real-time tracking for all the '
            'future frames.'
        )
        editToolBar.addAction(self.reinitLastSegmFrameAction)
        editToolBar.setVisible(False)
        self.reinitLastSegmFrameAction.toolbar = editToolBar
        self.functionsNotTested3D.append(self.reinitLastSegmFrameAction)

        # Edit toolbar
        modeToolBar = QToolBar("Mode", self)
        self.addToolBar(modeToolBar)

        self.modeComboBox = QComboBox()
        self.modeItems = [
            'Segmentation and Tracking',
            'Cell cycle analysis',
            'Viewer',
            'Custom annotations'
        ]
        self.modeComboBox.addItems(self.modeItems)
        self.modeComboBoxLabel = QLabel('    Mode: ')
        self.modeComboBoxLabel.setBuddy(self.modeComboBox)
        modeToolBar.addWidget(self.modeComboBoxLabel)
        modeToolBar.addWidget(self.modeComboBox)
        modeToolBar.setVisible(False)
        
        self.modeActionGroup = QActionGroup(self.modeMenu)
        for mode in self.modeItems:
            action = QAction(mode)
            action.setCheckable(True)
            self.modeActionGroup.addAction(action)
            self.modeMenu.addAction(action)
            if mode == 'Viewer':
                action.setChecked(True)

        self.modeToolBar = modeToolBar
        self.editToolBar = editToolBar
        self.editToolBar.setVisible(False)
        self.navigateToolBar.setVisible(False)

        self.gui_populateToolSettingsMenu()

        self.gui_createAnnotateToolbar()

        # toolbarSize = 58
        # fileToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        # navigateToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        # ccaToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        # editToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        # brushEraserToolBar.setIconSize(QSize(toolbarSize, toolbarSize))
        # modeToolBar.setIconSize(QSize(toolbarSize, toolbarSize))

    def gui_createAnnotateToolbar(self):
        # Edit toolbar
        self.annotateToolbar = QToolBar("Custom annotations", self)
        self.annotateToolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(Qt.LeftToolBarArea, self.annotateToolbar)
        self.annotateToolbar.addAction(self.loadCustomAnnotationsAction)
        self.annotateToolbar.addAction(self.addCustomAnnotationAction)
        self.annotateToolbar.addAction(self.viewAllCustomAnnotAction)
        self.annotateToolbar.setVisible(False)

    def gui_createLazyLoader(self):
        if not self.lazyLoader is None:
            return

        self.lazyLoaderThread = QThread()
        self.lazyLoaderMutex = QMutex()
        self.lazyLoaderWaitCond = QWaitCondition()
        self.waitReadH5cond = QWaitCondition()
        self.readH5mutex = QMutex()
        self.lazyLoader = workers.LazyLoader(
            self.lazyLoaderMutex, self.lazyLoaderWaitCond, 
            self.waitReadH5cond, self.readH5mutex
        )
        self.lazyLoader.moveToThread(self.lazyLoaderThread)
        self.lazyLoader.wait = True

        self.lazyLoader.signals.finished.connect(self.lazyLoaderThread.quit)
        self.lazyLoader.signals.finished.connect(self.lazyLoader.deleteLater)
        self.lazyLoaderThread.finished.connect(self.lazyLoaderThread.deleteLater)

        self.lazyLoader.signals.progress.connect(self.workerProgress)
        self.lazyLoader.signals.sigLoadingNewChunk.connect(self.loadingNewChunk)
        self.lazyLoader.sigLoadingFinished.connect(self.lazyLoaderFinished)
        self.lazyLoader.signals.critical.connect(self.lazyLoaderCritical)
        self.lazyLoader.signals.finished.connect(self.lazyLoaderWorkerClosed)

        self.lazyLoaderThread.started.connect(self.lazyLoader.run)
        self.lazyLoaderThread.start()
    
    def gui_createStoreStateWorker(self):
        self.storeStateWorker = None
        return
        self.storeStateThread = QThread()
        self.autoSaveMutex = QMutex()
        self.autoSaveWaitCond = QWaitCondition()

        self.storeStateWorker = workers.StoreGuiStateWorker(
            self.autoSaveMutex, self.autoSaveWaitCond
        )

        self.storeStateWorker.moveToThread(self.storeStateThread)
        self.storeStateWorker.finished.connect(self.storeStateThread.quit)
        self.storeStateWorker.finished.connect(self.storeStateWorker.deleteLater)
        self.storeStateThread.finished.connect(self.storeStateThread.deleteLater)

        self.storeStateWorker.sigDone.connect(self.storeStateWorkerDone)
        self.storeStateWorker.progress.connect(self.workerProgress)
        self.storeStateWorker.finished.connect(self.storeStateWorkerClosed)
        
        self.storeStateThread.started.connect(self.storeStateWorker.run)
        self.storeStateThread.start()

        self.logger.info('Store state worker started.')
    
    def storeStateWorkerDone(self):
        if self.storeStateWorker.callbackOnDone is not None:
            self.storeStateWorker.callbackOnDone()
        self.storeStateWorker.callbackOnDone = None

    def storeStateWorkerClosed(self):
        self.logger.info('Store state worker started.')
    
    def gui_createAutoSaveWorker(self):        
        if not hasattr(self, 'data'):
            return
        
        if not self.dataIsLoaded:
            return 
        
        if self.autoSaveActiveWorkers:
            garbage = self.autoSaveActiveWorkers[-1]
            self.autoSaveGarbageWorkers.append(garbage)
            worker = garbage[0]
            worker._stop()

        posData = self.data[self.pos_i]
        autoSaveThread = QThread()
        self.autoSaveMutex = QMutex()
        self.autoSaveWaitCond = QWaitCondition()

        savedSegmData = posData.segm_data.copy()
        autoSaveWorker = workers.AutoSaveWorker(
            self.autoSaveMutex, self.autoSaveWaitCond, savedSegmData
        )
        autoSaveWorker.isAutoSaveON = self.autoSaveToggle.isChecked()

        autoSaveWorker.moveToThread(autoSaveThread)
        autoSaveWorker.finished.connect(autoSaveThread.quit)
        autoSaveWorker.finished.connect(autoSaveWorker.deleteLater)
        autoSaveThread.finished.connect(autoSaveThread.deleteLater)

        autoSaveWorker.sigDone.connect(self.autoSaveWorkerDone)
        autoSaveWorker.progress.connect(self.workerProgress)
        autoSaveWorker.finished.connect(self.autoSaveWorkerClosed)
        autoSaveWorker.sigAutoSaveCannotProceed.connect(
            self.turnOffAutoSaveWorker
        )
        
        autoSaveThread.started.connect(autoSaveWorker.run)
        autoSaveThread.start()

        self.autoSaveActiveWorkers.append((autoSaveWorker, autoSaveThread))

        self.logger.info('Autosaving worker started.')
    
    def autoSaveWorkerStartTimer(self, worker, posData):
        self.autoSaveWorkerTimer = QTimer()
        self.autoSaveWorkerTimer.timeout.connect(
            partial(self.autoSaveWorkerTimerCallback, worker, posData)
        )
        self.autoSaveWorkerTimer.start(150)
    
    def autoSaveWorkerTimerCallback(self, worker, posData):
        if not self.isSaving:
            self.autoSaveWorkerTimer.stop()
            worker._enqueue(posData)
    
    def autoSaveWorkerDone(self):
        self.setSaturBarLabel(log=False)
    
    def autoSaveWorkerClosed(self, worker):
        if self.autoSaveActiveWorkers:
            self.logger.info('Autosaving worker closed.')
            try:
                self.autoSaveActiveWorkers.remove(worker)
            except Exception as e:
                pass

    def gui_createMainLayout(self):
        mainLayout = QGridLayout()
        row, col = 0, 1 # Leave column 1 for the overlay labels gradient editor
        mainLayout.addLayout(self.leftSideDocksLayout, row, col, 2, 1)

        row = 0
        col = 2
        mainLayout.addWidget(self.graphLayout, row, col, 1, 2)
        mainLayout.setRowStretch(row, 2)

        col = 4 # graphLayout spans two columns
        mainLayout.addWidget(self.labelsGrad, row, col)

        col = 5 
        mainLayout.addLayout(self.rightSideDocksLayout, row, col, 2, 1)

        col = 2
        row += 1
        self.resizeBottomLayoutLine = widgets.VerticalResizeHline()
        mainLayout.addWidget(self.resizeBottomLayoutLine, row, col, 1, 2)
        self.resizeBottomLayoutLine.dragged.connect(
            self.resizeBottomLayoutLineDragged
        )
        self.resizeBottomLayoutLine.clicked.connect(
            self.resizeBottomLayoutLineClicked
        )
        self.resizeBottomLayoutLine.released.connect(
            self.resizeBottomLayoutLineReleased
        )

        # row += 1
        # mainLayout.addItem(QSpacerItem(5,5), row+1, col, 1, 2)

        # row, col = 1, 2
        # mainLayout.addLayout(
        #     self.bottomLayout, row, col, 1, 2, alignment=Qt.AlignLeft
        # )

        row += 1
        mainLayout.addWidget(self.bottomScrollArea, row, col, 1, 2)
        mainLayout.setRowStretch(row, 0)

        # row, col = 2, 1
        # mainLayout.addWidget(self.terminal, row, col, 1, 4)
        # self.terminal.hide()

        return mainLayout

    def gui_createRegionPropsDockWidget(self, side=Qt.LeftDockWidgetArea):
        self.propsDockWidget = QDockWidget('Cell-ACDC objects', self)
        self.guiTabControl = widgets.guiTabControl(self.propsDockWidget)

        # self.guiTabControl.setFont(_font)

        self.propsDockWidget.setWidget(self.guiTabControl)
        self.propsDockWidget.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetFloatable | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.propsDockWidget.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )

        self.addDockWidget(side, self.propsDockWidget)
        self.propsDockWidget.hide()

    def gui_createControlsToolbar(self):
        self.addToolBarBreak()
        
        # Widgets toolbar
        brushEraserToolBar = QToolBar("Widgets", self)
        self.addToolBar(Qt.TopToolBarArea, brushEraserToolBar)

        self.editIDspinbox = widgets.SpinBox()
        # self.editIDspinbox.setMaximum(2**32-1)
        editIDLabel = QLabel('   ID: ')
        self.editIDLabelAction = brushEraserToolBar.addWidget(editIDLabel)
        self.editIDspinboxAction = brushEraserToolBar.addWidget(self.editIDspinbox)
        self.editIDLabelAction.setVisible(False)
        self.editIDspinboxAction.setVisible(False)
        self.editIDspinboxAction.setDisabled(True)
        self.editIDLabelAction.setDisabled(True)

        brushEraserToolBar.addWidget(QLabel(' '))
        self.editIDcheckbox = QCheckBox('Auto-ID')
        self.editIDcheckbox.setChecked(True)
        self.editIDcheckboxAction = brushEraserToolBar.addWidget(self.editIDcheckbox)
        self.editIDcheckboxAction.setVisible(False)

        self.brushSizeSpinbox = widgets.SpinBox(disableKeyPress=True)
        self.brushSizeSpinbox.setValue(4)
        brushSizeLabel = QLabel('   Size: ')
        brushSizeLabel.setBuddy(self.brushSizeSpinbox)
        self.brushSizeLabelAction = brushEraserToolBar.addWidget(brushSizeLabel)
        self.brushSizeAction = brushEraserToolBar.addWidget(self.brushSizeSpinbox)
        self.brushSizeLabelAction.setVisible(False)
        self.brushSizeAction.setVisible(False)
        
        brushEraserToolBar.addWidget(QLabel('  '))
        self.brushAutoFillCheckbox = QCheckBox('Auto-fill holes')
        self.brushAutoFillAction = brushEraserToolBar.addWidget(
            self.brushAutoFillCheckbox
        )
        self.brushAutoFillAction.setVisible(False)
        if 'brushAutoFill' in self.df_settings.index:
            checked = self.df_settings.at['brushAutoFill', 'value'] == 'Yes'
            self.brushAutoFillCheckbox.setChecked(checked)
        
        brushEraserToolBar.addWidget(QLabel('  '))
        self.brushAutoHideCheckbox = QCheckBox('Hide objects when hovering')
        self.brushAutoHideAction = brushEraserToolBar.addWidget(
            self.brushAutoHideCheckbox
        )
        self.brushAutoHideCheckbox.setChecked(True)
        self.brushAutoHideAction.setVisible(False)
        if 'brushAutoHide' in self.df_settings.index:
            checked = self.df_settings.at['brushAutoHide', 'value'] == 'Yes'
            self.brushAutoHideCheckbox.setChecked(checked)
        
        brushEraserToolBar.setVisible(False)
        self.brushEraserToolBar = brushEraserToolBar

        self.wandControlsToolbar = QToolBar("Magic wand controls", self)
        self.wandToleranceSlider = widgets.sliderWithSpinBox(
            title='Tolerance', title_loc='in_line'
        )
        self.wandToleranceSlider.setValue(5)

        self.wandAutoFillCheckbox = QCheckBox('Auto-fill holes')

        col = 3
        self.wandToleranceSlider.layout.addWidget(
            self.wandAutoFillCheckbox, 0, col
        )

        col += 1
        self.wandToleranceSlider.layout.setColumnStretch(col, 21)

        self.wandControlsToolbar.addWidget(self.wandToleranceSlider)

        self.addToolBar(Qt.TopToolBarArea , self.wandControlsToolbar)
        self.wandControlsToolbar.setVisible(False)

        separatorW = 5
        self.labelRoiToolbar = QToolBar("Magic labeller controls", self)
        self.labelRoiToolbar.addWidget(QLabel('ROI depth (n. of z-slices): '))
        self.labelRoiZdepthSpinbox = widgets.SpinBox(disableKeyPress=True)
        self.labelRoiToolbar.addWidget(self.labelRoiZdepthSpinbox)

        self.labelRoiToolbar.addWidget(widgets.QHWidgetSpacer(width=separatorW))
        self.labelRoiToolbar.addWidget(widgets.QVLine())
        self.labelRoiToolbar.addWidget(widgets.QHWidgetSpacer(width=separatorW))

        self.labelRoiReplaceExistingObjectsCheckbox = QCheckBox(
            'Remove existing objects touched by new objects'
        )
        self.labelRoiToolbar.addWidget(self.labelRoiReplaceExistingObjectsCheckbox)
        self.labelRoiAutoClearBorderCheckbox = QCheckBox(
            'Clear ROI borders before adding new objects'
        )
        self.labelRoiAutoClearBorderCheckbox.setChecked(True)
        self.labelRoiToolbar.addWidget(self.labelRoiAutoClearBorderCheckbox)
        
        self.labelRoiToolbar.addWidget(widgets.QHWidgetSpacer(width=separatorW))
        self.labelRoiToolbar.addWidget(widgets.QVLine())
        self.labelRoiToolbar.addWidget(widgets.QHWidgetSpacer(width=separatorW))

        group = QButtonGroup()
        group.setExclusive(True)
        self.labelRoiIsRectRadioButton = QRadioButton('Rectangular ROI')
        self.labelRoiIsRectRadioButton.setChecked(True)
        self.labelRoiIsFreeHandRadioButton = QRadioButton('Freehand ROI')
        self.labelRoiIsCircularRadioButton = QRadioButton('Circular ROI')
        group.addButton(self.labelRoiIsRectRadioButton)
        group.addButton(self.labelRoiIsFreeHandRadioButton)
        group.addButton(self.labelRoiIsCircularRadioButton)
        self.labelRoiToolbar.addWidget(self.labelRoiIsRectRadioButton)
        self.labelRoiToolbar.addWidget(self.labelRoiIsFreeHandRadioButton)
        self.labelRoiToolbar.addWidget(self.labelRoiIsCircularRadioButton)
        self.labelRoiToolbar.addWidget(QLabel(' Circular ROI radius (pixel): '))
        self.labelRoiCircularRadiusSpinbox = widgets.SpinBox(disableKeyPress=True)
        self.labelRoiCircularRadiusSpinbox.setMinimum(1)
        self.labelRoiCircularRadiusSpinbox.setValue(11)
        self.labelRoiCircularRadiusSpinbox.setDisabled(True)
        self.labelRoiToolbar.addWidget(self.labelRoiCircularRadiusSpinbox)
        
        self.labelRoiToolbar.addWidget(widgets.QHWidgetSpacer(width=separatorW))
        self.labelRoiToolbar.addWidget(widgets.QVLine())
        self.labelRoiToolbar.addWidget(widgets.QHWidgetSpacer(width=separatorW))

        startFrameLabel = QLabel('Start frame n. ')
        startFrameLabel.setDisabled(True)
        self.labelRoiToolbar.addWidget(startFrameLabel)
        self.labelRoiStartFrameNoSpinbox = widgets.SpinBox(disableKeyPress=True)
        self.labelRoiStartFrameNoSpinbox.label = startFrameLabel
        self.labelRoiStartFrameNoSpinbox.setValue(1)
        self.labelRoiStartFrameNoSpinbox.setMinimum(1)
        self.labelRoiToolbar.addWidget(self.labelRoiStartFrameNoSpinbox)
        self.labelRoiStartFrameNoSpinbox.setDisabled(True)

        self.labelRoiFromCurrentFrameAction = QAction(self)
        self.labelRoiFromCurrentFrameAction.setText('Segment from current frame')
        self.labelRoiFromCurrentFrameAction.setIcon(QIcon(":frames_current.svg"))
        self.labelRoiToolbar.addAction(self.labelRoiFromCurrentFrameAction)
        self.labelRoiFromCurrentFrameAction.setDisabled(True)

        self.labelRoiToolbar.addWidget(widgets.QHWidgetSpacer(width=3))
        stopFrameLabel = QLabel(' Stop frame n. ')
        stopFrameLabel.setDisabled(True)
        self.labelRoiToolbar.addWidget(stopFrameLabel)
        self.labelRoiStopFrameNoSpinbox = widgets.SpinBox(disableKeyPress=True)
        self.labelRoiStopFrameNoSpinbox.label = stopFrameLabel
        self.labelRoiStopFrameNoSpinbox.setValue(1)
        self.labelRoiStopFrameNoSpinbox.setMinimum(1)
        self.labelRoiToolbar.addWidget(self.labelRoiStopFrameNoSpinbox)
        self.labelRoiStopFrameNoSpinbox.setDisabled(True)

        self.labelRoiToEndFramesAction = QAction(self)
        self.labelRoiToEndFramesAction.setText('Segment all remaining frames')
        self.labelRoiToEndFramesAction.setIcon(QIcon(":frames_end.svg"))
        self.labelRoiToolbar.addAction(self.labelRoiToEndFramesAction)
        self.labelRoiToEndFramesAction.setDisabled(True)

        self.labelRoiTrangeCheckbox = QCheckBox('Segment range of frames')
        self.labelRoiToolbar.addWidget(self.labelRoiTrangeCheckbox)

        self.labelRoiViewCurrentModelAction = QAction(self)
        self.labelRoiViewCurrentModelAction.setText(
            'View current model\'s parameters'
        )
        self.labelRoiViewCurrentModelAction.setIcon(QIcon(":view.svg"))
        self.labelRoiToolbar.addAction(self.labelRoiViewCurrentModelAction)
        self.labelRoiViewCurrentModelAction.setDisabled(True)

        self.addToolBar(Qt.TopToolBarArea, self.labelRoiToolbar)
        self.labelRoiToolbar.setVisible(False)
        self.labelRoiTypesGroup = group

        self.loadLabelRoiLastParams()

        self.labelRoiTrangeCheckbox.toggled.connect(
            self.lebelRoiTrangeCheckboxToggled
        )
        self.labelRoiReplaceExistingObjectsCheckbox.toggled.connect(
            self.storeLabelRoiParams
        )
        self.labelRoiIsCircularRadioButton.toggled.connect(
            self.labelRoiIsCircularRadioButtonToggled
        )
        self.labelRoiCircularRadiusSpinbox.valueChanged.connect(
            self.updateLabelRoiCircularSize
        )
        self.labelRoiCircularRadiusSpinbox.valueChanged.connect(
            self.storeLabelRoiParams
        )
        self.labelRoiZdepthSpinbox.valueChanged.connect(
            self.storeLabelRoiParams
        )
        self.labelRoiAutoClearBorderCheckbox.toggled.connect(
            self.storeLabelRoiParams
        )
        group.buttonToggled.connect(self.storeLabelRoiParams)

        self.labelRoiToEndFramesAction.triggered.connect(
            self.labelRoiToEndFramesTriggered
        )
        self.labelRoiFromCurrentFrameAction.triggered.connect(
            self.labelRoiFromCurrentFrameTriggered
        )
        self.labelRoiViewCurrentModelAction.triggered.connect(
            self.labelRoiViewCurrentModel
        )

        self.keepIDsToolbar = QToolBar("Keep IDs controls", self)
        self.keepIDsConfirmAction = QAction()
        self.keepIDsConfirmAction.setIcon(QIcon(":greenTick.svg"))
        self.keepIDsConfirmAction.setToolTip('Apply "keep IDs" selection')
        self.keepIDsConfirmAction.setDisabled(True)
        self.keepIDsToolbar.addAction(self.keepIDsConfirmAction)
        self.keepIDsToolbar.addWidget(QLabel('  IDs to keep: '))
        instructionsText = (
            ' (Separate IDs by comma. Use a dash to denote a range of IDs)'
        )
        instructionsLabel = QLabel(instructionsText)
        self.keptIDsLineEdit = widgets.KeepIDsLineEdit(
            instructionsLabel, parent=self
        )
        self.keepIDsToolbar.addWidget(self.keptIDsLineEdit)
        self.keepIDsToolbar.addWidget(instructionsLabel)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.keepIDsToolbar.addWidget(spacer)
        self.addToolBar(Qt.TopToolBarArea, self.keepIDsToolbar)
        self.keepIDsToolbar.setVisible(False)

        self.keptIDsLineEdit.sigIDsChanged.connect(self.updateKeepIDs)
        self.keepIDsConfirmAction.triggered.connect(self.applyKeepObjects)
        
        self.autoPilotZoomToObjToolbar = QToolBar("Auto-zoom to objects", self)
        self.autoPilotZoomToObjToolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(Qt.TopToolBarArea, self.autoPilotZoomToObjToolbar)
        # self.autoPilotZoomToObjToolbar.setIconSize(QSize(16, 16))
        self.autoPilotZoomToObjToolbar.setVisible(False)
        
        closeToolbarAction = QAction(
            QIcon(":cancelButton.svg"), "Close toolbar...", self
        )
        closeToolbarAction.triggered.connect(self.closeToolbars)
        self.autoPilotZoomToObjToolbar.addAction(closeToolbarAction)
        
        self.autoPilotZoomToObjToolbar.addWidget(widgets.QVLine())
        self.autoPilotZoomToObjToolbar.addWidget(widgets.QHWidgetSpacer(width=separatorW))
        
        spinBox = widgets.SpinBox()
        spinBox.setMinimum(1)
        spinBox.label = QLabel('  Zoom to ID: ')
        spinBox.labelAction = self.autoPilotZoomToObjToolbar.addWidget(spinBox.label)
        spinBox.action = self.autoPilotZoomToObjToolbar.addWidget(spinBox)
        spinBox.editingFinished.connect(self.zoomToObj)
        spinBox.sigUpClicked.connect(self.autoZoomNextObj)
        spinBox.sigDownClicked.connect(self.autoZoomPrevObj)
        self.autoPilotZoomToObjSpinBox = spinBox
        toggle = widgets.Toggle()
        self.autoPilotZoomToObjToggle = toggle
        toggle.toggled.connect(self.autoPilotZoomToObjToggled)
        toggle.label = QLabel('  Auto-pilot: ')
        tooltip = (
            'When auto-pilot is active, you can use Up/Down arrows to '
            'automatically zoom to the next/previous object.\n\n'
            'Alternatively, you can type the ID of the object you want to '
            'zoom to.'
        )
        toggle.label.setToolTip(tooltip)
        toggle.setToolTip(tooltip)
        self.autoPilotZoomToObjToolbar.addWidget(toggle.label)
        self.autoPilotZoomToObjToolbar.addWidget(toggle)
        
        self.pointsLayersToolbar = QToolBar("Points layers", self)
        self.pointsLayersToolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(Qt.TopToolBarArea, self.pointsLayersToolbar)
        self.pointsLayersToolbar.addWidget(QLabel('Points layers:  '))
        # self.pointsLayersToolbar.setIconSize(QSize(16, 16))
        self.pointsLayersToolbar.setVisible(False)
        
        closeToolbarAction.toolbars = (
            self.pointsLayersToolbar, self.autoPilotZoomToObjToolbar
        )

        self.manualTrackingToolbar = widgets.ManualTrackingToolBar(
            "Manual tracking controls", self
        )
        self.manualTrackingToolbar.sigIDchanged.connect(self.initGhostObject)
        self.manualTrackingToolbar.sigDisableGhost.connect(self.clearGhost)
        self.manualTrackingToolbar.sigClearGhostContour.connect(
            self.clearGhostContour
        )
        self.manualTrackingToolbar.sigClearGhostMask.connect(
            self.clearGhostMask
        )
        self.manualTrackingToolbar.sigGhostOpacityChanged.connect(
            self.updateGhostMaskOpacity
        )

        self.addToolBar(Qt.TopToolBarArea, self.manualTrackingToolbar)
        self.manualTrackingToolbar.setVisible(False)

    def gui_populateToolSettingsMenu(self):
        brushHoverModeActionGroup = QActionGroup(self)
        brushHoverModeActionGroup.setExclusionPolicy(
            QActionGroup.ExclusionPolicy.Exclusive
        )
        self.brushHoverCenterModeAction = QAction()
        self.brushHoverCenterModeAction.setCheckable(True)
        self.brushHoverCenterModeAction.setText(
            'Use center of the brush/eraser cursor to determine hover ID'
        )
        self.brushHoverCircleModeAction = QAction()
        self.brushHoverCircleModeAction.setCheckable(True)
        self.brushHoverCircleModeAction.setText(
            'Use the entire circle of the brush/eraser cursor to determine hover ID'
        )
        brushHoverModeActionGroup.addAction(self.brushHoverCenterModeAction)
        brushHoverModeActionGroup.addAction(self.brushHoverCircleModeAction)
        brushHoverModeMenu = self.settingsMenu.addMenu(
            'Brush/eraser cursor hovering mode'
        )
        brushHoverModeMenu.addAction(self.brushHoverCenterModeAction)
        brushHoverModeMenu.addAction(self.brushHoverCircleModeAction)

        if 'useCenterBrushCursorHoverID' not in self.df_settings.index:
            self.df_settings.at['useCenterBrushCursorHoverID', 'value'] = 'Yes'

        useCenterBrushCursorHoverID = self.df_settings.at[
            'useCenterBrushCursorHoverID', 'value'
        ] == 'Yes'
        self.brushHoverCenterModeAction.setChecked(useCenterBrushCursorHoverID)
        self.brushHoverCircleModeAction.setChecked(not useCenterBrushCursorHoverID)

        self.brushHoverCenterModeAction.toggled.connect(
            self.useCenterBrushCursorHoverIDtoggled
        )

        self.settingsMenu.addSeparator()

        for button in self.checkableQButtonsGroup.buttons():
            toolName = re.findall('Toggle "(.*)"', button.toolTip())[0]
            menu = self.settingsMenu.addMenu(f'{toolName} tool')
            action = QAction(button)
            action.setText('Keep tool active after using it')
            action.setCheckable(True)
            if toolName in self.df_settings.index:
                action.setChecked(True)
            action.toggled.connect(self.keepToolActiveActionToggled)
            menu.addAction(action)

        self.warnLostCellsAction = QAction()
        self.warnLostCellsAction.setText('Show pop-up warning for lost cells')
        self.warnLostCellsAction.setCheckable(True)
        self.warnLostCellsAction.setChecked(True)
        self.settingsMenu.addAction(self.warnLostCellsAction)

        warnEditingWithAnnotTexts = {
            'Delete ID': 'Show warning when deleting ID that has annotations',
            'Separate IDs': 'Show warning when separating IDs that have annotations',
            'Edit ID': 'Show warning when editing ID that has annotations',
            'Annotate ID as dead':
                'Show warning when annotating dead ID that has annotations',
            'Delete ID with eraser':
                'Show warning when erasing ID that has annotations',
            'Add new ID with brush tool':
                'Show warning when adding new ID (brush) that has annotations',
            'Merge IDs':
                'Show warning when merging IDs that have annotations',
            'Add new ID with curvature tool':
                'Show warning when adding new ID (curv. tool) that has annotations',
            'Add new ID with magic-wand':
                'Show warning when adding new ID (magic-wand) that has annotations',
            'Delete IDs using ROI':
                'Show warning when using ROIs to delete IDs that have annotations',
        }
        self.warnEditingWithAnnotActions = {}
        for key, desc in warnEditingWithAnnotTexts.items():
            action = QAction()
            action.setText(desc)
            action.setCheckable(True)
            action.setChecked(True)
            action.removeAnnot = False
            self.warnEditingWithAnnotActions[key] = action
            self.settingsMenu.addAction(action)


    def gui_createStatusBar(self):
        self.statusbar = self.statusBar()
        # Permanent widget
        self.wcLabel = QLabel('')
        self.statusbar.addPermanentWidget(self.wcLabel)

        self.toggleTerminalButton = widgets.ToggleTerminalButton()
        self.statusbar.addWidget(self.toggleTerminalButton)
        self.toggleTerminalButton.sigClicked.connect(
            self.gui_terminalButtonClicked
        )

        self.statusBarLabel = QLabel('')
        self.statusbar.addWidget(self.statusBarLabel)
    
    def gui_createTerminalWidget(self):
        self.terminal = widgets.QLog(logger=self.logger)
        self.terminal.connect()
        self.terminalDock = QDockWidget('Log', self)

        self.terminalDock.setWidget(self.terminal)
        self.terminalDock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetFloatable | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.terminalDock.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.terminalDock)
        # self.terminalDock.widget().layout().setContentsMargins(10,0,10,0)
        self.terminalDock.setVisible(False)
        
    def gui_terminalButtonClicked(self, terminalVisible):
        self.ax1_viewRange = self.ax1.vb.viewRange()
        self.terminalDock.setVisible(terminalVisible)
        QTimer.singleShot(200, self.resetRange)

    def gui_createActions(self):
        # File actions
        self.newAction = QAction(self)
        self.newAction.setText("&New")
        self.newAction.setIcon(QIcon(":file-new.svg"))
        self.openAction = QAction(
            QIcon(":folder-open.svg"), "&Load folder...", self
        )
        self.openFileAction = QAction(
            QIcon(":image.svg"),"&Open image/video file...", self
        )
        self.manageVersionsAction = QAction(
            QIcon(":manage_versions.svg"), "Load older versions...", self
        )
        self.manageVersionsAction.setToolTip(
            'Load an older version of the `acdc_output.csv` file (table '
            'with annotations and measurements).'
        )
        self.manageVersionsAction.setDisabled(True)
        self.saveAction = QAction(QIcon(":file-save.svg"), "Save", self)
        self.saveAsAction = QAction("Save as...", self)
        self.quickSaveAction = QAction("Save only segm. file", self)
        self.loadFluoAction = QAction("Load fluorescence images...", self)
        self.loadPosAction = QAction("Load different Position...", self)
        # self.reloadAction = QAction(
        #     QIcon(":reload.svg"), "Reload segmentation file", self
        # )
        self.nextAction = QAction('Next', self)
        self.prevAction = QAction('Previous', self)
        self.showInExplorerAction = QAction(
            QIcon(":drawer.svg"), f"&{self.openFolderText}", self
        )
        self.exitAction = QAction("&Exit", self)
        self.undoAction = QAction(QIcon(":undo.svg"), "Undo", self)
        self.redoAction = QAction(QIcon(":redo.svg"), "Redo", self)
        # String-based key sequences
        self.newAction.setShortcut("Ctrl+N")
        self.openAction.setShortcut("Ctrl+O")
        self.loadPosAction.setShortcut("Shift+P")
        self.saveAsAction.setShortcut("Ctrl+Shift+S")
        self.saveAction.setShortcut("Ctrl+Alt+S")
        self.quickSaveAction.setShortcut("Ctrl+S")
        self.undoAction.setShortcut("Ctrl+Z")
        self.redoAction.setShortcut("Ctrl+Y")
        self.nextAction.setShortcut(Qt.Key_Right)
        self.prevAction.setShortcut(Qt.Key_Left)
        self.addAction(self.nextAction)
        self.addAction(self.prevAction)
        # Help tips
        newTip = "Create a new segmentation file"
        self.newAction.setStatusTip(newTip)
        self.newAction.setToolTip(newTip)
        self.newAction.setWhatsThis("Create a new empty segmentation file")

        self.findIdAction = QAction(self)
        self.findIdAction.setIcon(QIcon(":find.svg"))
        self.findIdAction.setShortcut('Ctrl+F')
        self.findIdAction.setToolTip(
            'Find and highlight ID (Ctrl+F).'
            'Press "Esc" to clear highlighted object.'
        )

        # Edit actions
        models = myutils.get_list_of_models()
        self.segmActions = []
        self.modelNames = []
        self.acdcSegment_li = []
        self.models = []
        for model_name in models:
            action = QAction(f"{model_name}...")
            self.segmActions.append(action)
            self.modelNames.append(model_name)
            self.models.append(None)
            self.acdcSegment_li.append(None)
            action.setDisabled(True)

        self.addCustomModelFrameAction = QAction('Add custom model...', self)
        self.addCustomModelVideoAction = QAction('Add custom model...', self)

        self.segmActionsVideo = []
        for model_name in models:
            action = QAction(f"{model_name}...")
            self.segmActionsVideo.append(action)
            action.setDisabled(True)
        self.SegmActionRW = QAction("Random walker...", self)
        self.SegmActionRW.setDisabled(True)

        self.postProcessSegmAction = QAction(
            "Segmentation post-processing...", self
        )
        self.postProcessSegmAction.setDisabled(True)
        self.postProcessSegmAction.setCheckable(True)

        self.repeatTrackingAction = QAction(
            QIcon(":repeat-tracking.svg"), "Repeat tracking", self
        )
        self.repeatTrackingAction.setToolTip(
            'Repeat tracking on current frame\n'
            'SHORTCUT: "Shift+T"'
        )

        self.repeatTrackingMenuAction = QAction(
            'Repeat tracking on current frame...', self
        )
        self.repeatTrackingMenuAction.setDisabled(True)
        self.repeatTrackingMenuAction.setShortcut('Shift+T')

        self.repeatTrackingVideoAction = QAction(
            'Repeat tracking on multiple frames...', self
        )
        self.repeatTrackingVideoAction.setDisabled(True)
        self.repeatTrackingVideoAction.setShortcut('Alt+Shift+T')

        self.trackingAlgosGroup = QActionGroup(self)
        self.trackWithAcdcAction = QAction('Cell-ACDC', self)
        self.trackWithAcdcAction.setCheckable(True)
        self.trackingAlgosGroup.addAction(self.trackWithAcdcAction)

        self.trackWithYeazAction = QAction('YeaZ', self)
        self.trackWithYeazAction.setCheckable(True)
        self.trackingAlgosGroup.addAction(self.trackWithYeazAction)

        rt_trackers = myutils.get_list_of_real_time_trackers()
        for rt_tracker in rt_trackers:
            rtTrackerAction = QAction(rt_tracker, self)
            rtTrackerAction.setCheckable(True)
            self.trackingAlgosGroup.addAction(rtTrackerAction)

        self.trackWithAcdcAction.setChecked(True)
        if 'tracking_algorithm' in self.df_settings.index:
            trackingAlgo = self.df_settings.at['tracking_algorithm', 'value']
            if trackingAlgo == 'Cell-ACDC':
                self.trackWithAcdcAction.setChecked(True)
            elif trackingAlgo == 'YeaZ':
                self.trackWithYeazAction.setChecked(True)
            else:
                for rtTrackerAction in self.trackingAlgosGroup.actions():
                    if rtTrackerAction.text() == trackingAlgo:
                        rtTrackerAction.setChecked(True)
                        break

        self.setMeasurementsAction = QAction('Set measurements...')
        self.addCustomMetricAction = QAction('Add custom measurement...')
        self.addCombineMetricAction = QAction('Add combined measurement...')

        # Standard key sequence
        # self.copyAction.setShortcut(QKeySequence.StandardKey.Copy)
        # self.pasteAction.setShortcut(QKeySequence.StandardKey.Paste)
        # self.cutAction.setShortcut(QKeySequence.StandardKey.Cut)
        # Help actions
        self.tipsAction = QAction("Tips and tricks...", self)
        self.UserManualAction = QAction("User Manual...", self)
        self.aboutAction = QAction("About Cell-ACDC", self)
        # self.aboutAction = QAction("&About...", self)

        # Assign mother to bud button
        self.assignBudMothAutoAction = QAction(self)
        self.assignBudMothAutoAction.setIcon(QIcon(":autoAssign.svg"))
        self.assignBudMothAutoAction.setVisible(False)
        self.assignBudMothAutoAction.setToolTip(
            'Automatically assign buds to mothers using YeastMate'
        )

        self.editCcaToolAction = QAction(self)
        self.editCcaToolAction.setIcon(QIcon(":edit_cca.svg"))
        # self.editCcaToolAction.setDisabled(True)
        self.editCcaToolAction.setVisible(False)
        self.editCcaToolAction.setToolTip(
            'Manually edit cell cycle annotations table.'
        )

        self.reInitCcaAction = QAction(self)
        self.reInitCcaAction.setIcon(QIcon(":reinitCca.svg"))
        self.reInitCcaAction.setVisible(False)
        self.reInitCcaAction.setToolTip(
            'Re-initialize cell cycle annotations table from this frame onward.\n'
            'NOTE: This will erase all the already annotated future frames information\n'
            '(from the current session not the saved information)'
        )

        self.toggleColorSchemeAction = QAction(
            'Switch to light mode'
        )
        self.gui_updateSwitchColorSchemeActionText()
        
        self.editShortcutsAction = QAction(
            'Customize keyboard shortcuts...', self
        )
        self.editShortcutsAction.setShortcut('Ctrl+K')

        self.editTextIDsColorAction = QAction('Text annotation color...', self)
        self.editTextIDsColorAction.setDisabled(True)

        self.editOverlayColorAction = QAction('Overlay color...', self)
        self.editOverlayColorAction.setDisabled(True)

        self.manuallyEditCcaAction = QAction(
            'Edit cell cycle annotations...', self
        )
        self.manuallyEditCcaAction.setShortcut('Ctrl+Shift+P')
        self.manuallyEditCcaAction.setDisabled(True)

        self.viewCcaTableAction = QAction(
            'View cell cycle annotations...', self
        )
        self.viewCcaTableAction.setDisabled(True)
        self.viewCcaTableAction.setShortcut('Ctrl+P')

        self.invertBwAction = QAction('Invert black/white', self)
        self.invertBwAction.setCheckable(True)
        checked = self.df_settings.at['is_bw_inverted', 'value'] == 'Yes'
        self.invertBwAction.setChecked(checked)

        self.shuffleCmapAction =  QAction('Randomly shuffle colormap', self)
        self.shuffleCmapAction.setShortcut('Shift+S')

        self.greedyShuffleCmapAction =  QAction(
            'Optimise colormap', self
        )
        self.greedyShuffleCmapAction.setShortcut('Alt+Shift+S')

        self.saveLabColormapAction = QAction(
            'Save labels colormap...', self
        )

        self.normalizeRawAction = QAction(
            'Do not normalize. Display raw image', self)
        self.normalizeToFloatAction = QAction(
            'Convert to floating point format with values [0, 1]', self)
        # self.normalizeToUbyteAction = QAction(
        #     'Rescale to 8-bit unsigned integer format with values [0, 255]', self)
        self.normalizeRescale0to1Action = QAction(
            'Rescale to [0, 1]', self)
        self.normalizeByMaxAction = QAction(
            'Normalize by max value', self)
        self.normalizeRawAction.setCheckable(True)
        self.normalizeToFloatAction.setCheckable(True)
        # self.normalizeToUbyteAction.setCheckable(True)
        self.normalizeRescale0to1Action.setCheckable(True)
        self.normalizeByMaxAction.setCheckable(True)
        self.normalizeQActionGroup = QActionGroup(self)
        self.normalizeQActionGroup.addAction(self.normalizeRawAction)
        self.normalizeQActionGroup.addAction(self.normalizeToFloatAction)
        # self.normalizeQActionGroup.addAction(self.normalizeToUbyteAction)
        self.normalizeQActionGroup.addAction(self.normalizeRescale0to1Action)
        self.normalizeQActionGroup.addAction(self.normalizeByMaxAction)

        self.zoomToObjsAction = QAction(
            'Zoom to objects  (shortcut: H key)', self
        )
        self.zoomOutAction = QAction(
            'Zoom out  (shortcut: double press H key)', self
        )

        self.relabelSequentialAction = QAction(
            'Relabel IDs sequentially...', self
        )
        self.relabelSequentialAction.setShortcut('Ctrl+L')
        self.relabelSequentialAction.setDisabled(True)

        self.setLastUserNormAction()

        self.autoSegmAction = QAction(
            'Enable automatic segmentation', self)
        self.autoSegmAction.setCheckable(True)
        self.autoSegmAction.setDisabled(True)

        self.enableSmartTrackAction = QAction(
            'Smart handling of enabling/disabling tracking', self)
        self.enableSmartTrackAction.setCheckable(True)
        self.enableSmartTrackAction.setChecked(True)

        self.enableAutoZoomToCellsAction = QAction(
            'Automatic zoom to all cells when pressing "Next/Previous"', self)
        self.enableAutoZoomToCellsAction.setCheckable(True)

        gaussBlurAction = QAction('Gaussian blur...', self)
        gaussBlurAction.setCheckable(True)
        name = 'Gaussian blur'
        gaussBlurAction.filterName = name
        self.filtersWins[name] = {}
        self.filtersWins[name]['action'] = gaussBlurAction
        self.filtersWins[name]['dialogueApp'] = filters.gaussBlurDialog
        self.filtersWins[name]['window'] = None

        diffGaussFilterAction = QAction('Sharpen (DoG filter)...', self)
        diffGaussFilterAction.setCheckable(True)
        name = 'Sharpen (DoG filter)'
        diffGaussFilterAction.filterName = name
        self.filtersWins[name] = {}
        self.filtersWins[name]['action'] = diffGaussFilterAction
        self.filtersWins[name]['dialogueApp'] = filters.diffGaussFilterDialog
        self.filtersWins[name]['initMethods'] = {'initSpotmaxValues': ['posData']}
        self.filtersWins[name]['window'] = None

        edgeDetectorAction = QAction('Edge detection...', self)
        edgeDetectorAction.setCheckable(True)      
        name = 'Edge detection filter'
        edgeDetectorAction.filterName = name
        self.filtersWins[name] = {}
        self.filtersWins[name]['action'] = edgeDetectorAction
        self.filtersWins[name]['dialogueApp'] = filters.edgeDetectionDialog
        self.filtersWins[name]['window'] = None

        entropyFilterAction = QAction(
            'Object detection (entropy filter)...', self
        )
        entropyFilterAction.setCheckable(True)
        name = 'Object detection filter'
        entropyFilterAction.filterName = name
        self.filtersWins[name] = {}
        self.filtersWins[name]['action'] = entropyFilterAction
        self.filtersWins[name]['dialogueApp'] = filters.entropyFilterDialog
        self.filtersWins[name]['window'] = None

        self.imgPropertiesAction = QAction('Properties...', self)
        self.imgPropertiesAction.setDisabled(True)

        self.addDelRoiAction = QAction(self)
        self.addDelRoiAction.roiType = 'rect'
        self.addDelRoiAction.setIcon(QIcon(":addDelRoi.svg"))
        self.addDelRoiAction.setToolTip(
            'Add resizable rectangle. Every ID touched by the rectangle will be '
            'automaticaly deleted.\n '
            'Moving adn resizing the rectangle will restore deleted IDs if they are not '
            'touched by it anymore.\n'
            'To delete rectangle right-click on it --> remove.')
        
        
        self.addDelPolyLineRoiAction = QAction(self)
        self.addDelPolyLineRoiAction.setCheckable(True)
        self.addDelPolyLineRoiAction.roiType = 'polyline'
        self.addDelPolyLineRoiAction.setIcon(QIcon(":addDelPolyLineRoi.svg"))
        self.addDelPolyLineRoiAction.setToolTip(
            'Add custom poly-line deletion ROI. Every ID touched by the ROI will be '
            'automaticaly deleted.\n\n'
            'USAGE:\n'
            '- Activate the button.\n'
            '- Left-click on the LEFT image to add a new anchor point.\n'
            '- Add as many anchor points as needed and then close by clicking on starting anchor.\n'
            '- Delete an anchor-point with right-click on it.\n'
            '- Add a new anchor point on an existing segment with right-click on the segment.\n\n'
            'Moving and reshaping the ROI will restore deleted IDs if they are not '
            'touched by it anymore.\n'
            'To delete the ROI right-click on it --> remove.'
        )
        self.checkableButtons.append(self.addDelPolyLineRoiAction)
        self.LeftClickButtons.append(self.addDelPolyLineRoiAction)
       

        self.delBorderObjAction = QAction(self)
        self.delBorderObjAction.setIcon(QIcon(":delBorderObj.svg"))
        self.delBorderObjAction.setToolTip(
            'Remove segmented objects touching the border of the image'
        )

        self.loadCustomAnnotationsAction = QAction(self)
        self.loadCustomAnnotationsAction.setIcon(QIcon(":load_annotation.svg"))
        self.loadCustomAnnotationsAction.setToolTip(
            'Load previously used custom annotations'
        )
    
        self.addCustomAnnotationAction = QAction(self)
        self.addCustomAnnotationAction.setIcon(QIcon(":annotate.svg"))
        self.addCustomAnnotationAction.setToolTip('Add custom annotation')
        # self.functionsNotTested3D.append(self.addCustomAnnotationAction)

        self.viewAllCustomAnnotAction = QAction(self)
        self.viewAllCustomAnnotAction.setCheckable(True)
        self.viewAllCustomAnnotAction.setIcon(QIcon(":eye.svg"))
        self.viewAllCustomAnnotAction.setToolTip('Show all custom annotations')
        # self.functionsNotTested3D.append(self.viewAllCustomAnnotAction)

        # self.imgGradLabelsAlphaUpAction = QAction(self)
        # self.imgGradLabelsAlphaUpAction.setVisible(False)
        # self.imgGradLabelsAlphaUpAction.setShortcut('Ctrl+Up')
    
    def gui_updateSwitchColorSchemeActionText(self):
        if self._colorScheme == 'dark':
            txt = 'Switch to light mode'
        else:
            txt = 'Switch to dark mode'
        self.toggleColorSchemeAction.setText(txt)

    def gui_connectActions(self):
        # Connect File actions
        self.newAction.triggered.connect(self.newFile)
        self.openAction.triggered.connect(self.openFolder)
        self.openFileAction.triggered.connect(self.openFile)
        self.manageVersionsAction.triggered.connect(self.manageVersions)
        self.saveAction.triggered.connect(self.saveData)
        self.saveAsAction.triggered.connect(self.saveAsData)
        self.quickSaveAction.triggered.connect(self.quickSave)
        self.autoSaveToggle.toggled.connect(self.autoSaveToggled)
        self.annotLostObjsToggle.toggled.connect(self.annotLostObjsToggled)
        self.highLowResToggle.clicked.connect(self.highLoweResClicked)
        self.showInExplorerAction.triggered.connect(self.showInExplorer_cb)
        self.exitAction.triggered.connect(self.close)
        self.undoAction.triggered.connect(self.undo)
        self.redoAction.triggered.connect(self.redo)
        self.nextAction.triggered.connect(self.nextActionTriggered)
        self.prevAction.triggered.connect(self.prevActionTriggered)

        self.toggleColorSchemeAction.triggered.connect(self.onToggleColorScheme)
        self.editShortcutsAction.triggered.connect(self.editShortcuts_cb)

        # Connect Help actions
        self.tipsAction.triggered.connect(self.showTipsAndTricks)
        self.UserManualAction.triggered.connect(myutils.showUserManual)
        self.aboutAction.triggered.connect(self.showAbout)
        # Connect Open Recent to dynamically populate it
        # self.openRecentMenu.aboutToShow.connect(self.populateOpenRecent)
        self.checkableQButtonsGroup.buttonClicked.connect(self.uncheckQButton)

        self.showPropsDockButton.sigClicked.connect(self.showPropsDockWidget)

        self.loadCustomAnnotationsAction.triggered.connect(
            self.loadCustomAnnotations
        )
        self.addCustomAnnotationAction.triggered.connect(
            self.addCustomAnnotation
        )
        self.viewAllCustomAnnotAction.toggled.connect(
            self.viewAllCustomAnnot
        )
        self.addCustomModelVideoAction.triggered.connect(
            self.showInstructionsCustomModel
        )
        self.addCustomModelFrameAction.triggered.connect(
            self.showInstructionsCustomModel
        )
        self.addCustomModelFrameAction.callback = self.segmFrameCallback
        self.addCustomModelVideoAction.callback = self.segmVideoCallback
    
    def onToggleColorScheme(self):
        if self.toggleColorSchemeAction.text().find('light') != -1:
            self._colorScheme = 'light'
            setDarkModeToggleChecked = False
        else:
            self._colorScheme = 'dark'
            setDarkModeToggleChecked = True
        self.gui_updateSwitchColorSchemeActionText()
        _warnings.warnRestartCellACDCcolorModeToggled(
            self._colorScheme, app_name=self._appName, parent=self
        )
        load.rename_qrc_resources_file(self._colorScheme)
        self.statusBarLabel.setText(html_utils.paragraph(
            f'<i>Restart {self._appName} for the change to take effect</i>', 
            font_color='red'
        ))
        self.df_settings.at['colorScheme', 'value'] = self._colorScheme
        self.df_settings.to_csv(settings_csv_path)
        
    def gui_connectEditActions(self):
        self.showInExplorerAction.setEnabled(True)
        self.setEnabledFileToolbar(True)
        self.loadFluoAction.setEnabled(True)
        self.isEditActionsConnected = True

        self.overlayButton.toggled.connect(self.overlay_cb)
        self.addPointsLayerAction.triggered.connect(self.addPointsLayer_cb)
        self.overlayLabelsButton.toggled.connect(self.overlayLabels_cb)
        self.overlayButton.sigRightClick.connect(self.showOverlayContextMenu)
        self.labelRoiButton.sigRightClick.connect(self.showLabelRoiContextMenu)
        self.overlayLabelsButton.sigRightClick.connect(
            self.showOverlayLabelsContextMenu
        )
        self.rulerButton.toggled.connect(self.ruler_cb)
        self.loadFluoAction.triggered.connect(self.loadFluo_cb)
        self.loadPosAction.triggered.connect(self.loadPosTriggered)
        # self.reloadAction.triggered.connect(self.reload_cb)
        self.findIdAction.triggered.connect(self.findID)
        self.slideshowButton.toggled.connect(self.launchSlideshow)

        self.segmSingleFrameMenu.triggered.connect(self.segmFrameCallback)
        self.segmVideoMenu.triggered.connect(self.segmVideoCallback)

        self.SegmActionRW.triggered.connect(self.randomWalkerSegm)
        self.postProcessSegmAction.toggled.connect(self.postProcessSegm)
        self.autoSegmAction.toggled.connect(self.autoSegm_cb)
        self.realTimeTrackingToggle.clicked.connect(self.realTimeTrackingClicked)
        self.repeatTrackingAction.triggered.connect(self.repeatTracking)
        self.manualTrackingButton.toggled.connect(self.manualTracking_cb)
        self.repeatTrackingMenuAction.triggered.connect(self.repeatTracking)
        self.repeatTrackingVideoAction.triggered.connect(
            self.repeatTrackingVideo
        )
        for rtTrackerAction in self.trackingAlgosGroup.actions():
            rtTrackerAction.toggled.connect(self.storeTrackingAlgo)

        self.brushButton.toggled.connect(self.Brush_cb)
        self.eraserButton.toggled.connect(self.Eraser_cb)
        self.curvToolButton.toggled.connect(self.curvTool_cb)
        self.wandToolButton.toggled.connect(self.wand_cb)
        self.labelRoiButton.toggled.connect(self.labelRoi_cb)
        self.reInitCcaAction.triggered.connect(self.reInitCca)
        self.moveLabelToolButton.toggled.connect(self.moveLabelButtonToggled)
        self.editCcaToolAction.triggered.connect(
            self.manualEditCcaToolbarActionTriggered
        )
        self.assignBudMothAutoAction.triggered.connect(
            self.autoAssignBud_YeastMate
        )
        self.keepIDsButton.toggled.connect(self.keepIDs_cb)

        self.expandLabelToolButton.toggled.connect(self.expandLabelCallback)

        self.reinitLastSegmFrameAction.triggered.connect(self.reInitLastSegmFrame)

        # self.repeatAutoCcaAction.triggered.connect(self.repeatAutoCca)
        self.manuallyEditCcaAction.triggered.connect(self.manualEditCca)
        self.invertBwAction.toggled.connect(self.invertBw)
        self.saveLabColormapAction.triggered.connect(self.saveLabelsColormap)

        self.enableSmartTrackAction.toggled.connect(self.enableSmartTrack)
        # Brush/Eraser size action
        self.brushSizeSpinbox.valueChanged.connect(self.brushSize_cb)
        self.editIDcheckbox.toggled.connect(self.autoIDtoggled)
        # Mode
        self.modeActionGroup.triggered.connect(self.changeModeFromMenu)
        self.modeComboBox.currentIndexChanged.connect(self.changeMode)
        self.modeComboBox.activated.connect(self.clearComboBoxFocus)
        self.equalizeHistPushButton.toggled.connect(self.equalizeHist)
        
        self.editOverlayColorAction.triggered.connect(self.toggleOverlayColorButton)
        self.editTextIDsColorAction.triggered.connect(self.toggleTextIDsColorButton)
        self.overlayColorButton.sigColorChanging.connect(self.changeOverlayColor)
        self.overlayColorButton.sigColorChanged.connect(self.saveOverlayColor)
        self.textIDsColorButton.sigColorChanging.connect(self.updateTextAnnotColor)
        self.textIDsColorButton.sigColorChanged.connect(self.saveTextIDsColors)

        self.setMeasurementsAction.triggered.connect(self.showSetMeasurements)
        self.addCustomMetricAction.triggered.connect(self.addCustomMetric)
        self.addCombineMetricAction.triggered.connect(self.addCombineMetric)

        self.labelsGrad.colorButton.sigColorChanging.connect(self.updateBkgrColor)
        self.labelsGrad.colorButton.sigColorChanged.connect(self.saveBkgrColor)
        self.labelsGrad.sigGradientChangeFinished.connect(self.updateLabelsCmap)
        self.labelsGrad.sigGradientChanged.connect(self.ticksCmapMoved)
        self.labelsGrad.textColorButton.sigColorChanging.connect(
            self.updateTextLabelsColor
        )
        self.labelsGrad.textColorButton.sigColorChanged.connect(
            self.saveTextLabelsColor
        )
        # self.addFontSizeActions(
        #     self.labelsGrad.fontSizeMenu, self.setFontSizeActionChecked
        # )

        self.labelsGrad.shuffleCmapAction.triggered.connect(self.shuffle_cmap)
        self.labelsGrad.greedyShuffleCmapAction.triggered.connect(
            self.greedyShuffleCmap
        )
        self.shuffleCmapAction.triggered.connect(self.shuffle_cmap)
        self.greedyShuffleCmapAction.triggered.connect(self.greedyShuffleCmap)
        self.labelsGrad.invertBwAction.toggled.connect(self.setCheckedInvertBW)
        self.labelsGrad.sigShowLabelsImgToggled.connect(self.showLabelImageItem)
        self.labelsGrad.sigShowRightImgToggled.connect(self.showRightImageItem)
        
        self.labelsGrad.defaultSettingsAction.triggered.connect(
            self.restoreDefaultSettings
        )

        # self.addFontSizeActions(
        #     self.imgGrad.fontSizeMenu, self.setFontSizeActionChecked
        # )
        self.imgGrad.invertBwAction.toggled.connect(self.setCheckedInvertBW)
        self.imgGrad.textColorButton.disconnect()
        self.imgGrad.textColorButton.clicked.connect(
            self.editTextIDsColorAction.trigger
        )
        self.imgGrad.labelsAlphaSlider.valueChanged.connect(
            self.updateLabelsAlpha
        )
        self.imgGrad.defaultSettingsAction.triggered.connect(
            self.restoreDefaultSettings
        )

        # Drawing mode
        self.drawIDsContComboBox.currentIndexChanged.connect(
            self.drawIDsContComboBox_cb
        )
        self.drawIDsContComboBox.activated.connect(self.clearComboBoxFocus)

        self.annotateRightHowCombobox.currentIndexChanged.connect(
            self.annotateRightHowCombobox_cb
        )
        self.annotateRightHowCombobox.activated.connect(self.clearComboBoxFocus)

        self.showTreeInfoCheckbox.toggled.connect(self.setAnnotInfoMode)

        # Left
        self.annotIDsCheckbox.clicked.connect(self.annotOptionClicked)
        self.annotCcaInfoCheckbox.clicked.connect(self.annotOptionClicked)
        self.annotContourCheckbox.clicked.connect(self.annotOptionClicked)
        self.annotSegmMasksCheckbox.clicked.connect(self.annotOptionClicked)
        self.drawMothBudLinesCheckbox.clicked.connect(self.annotOptionClicked)
        self.drawNothingCheckbox.clicked.connect(self.annotOptionClicked)
        self.annotNumZslicesCheckbox.clicked.connect(self.annotOptionClicked)

        # Right 
        self.annotIDsCheckboxRight.clicked.connect(
            self.annotOptionClickedRight)
        self.annotCcaInfoCheckboxRight.clicked.connect(
            self.annotOptionClickedRight)
        self.annotContourCheckboxRight.clicked.connect(
            self.annotOptionClickedRight)
        self.annotSegmMasksCheckboxRight.clicked.connect(
            self.annotOptionClickedRight)
        self.drawMothBudLinesCheckboxRight.clicked.connect(
            self.annotOptionClickedRight)
        self.drawNothingCheckboxRight.clicked.connect(
            self.annotOptionClickedRight)
        self.annotNumZslicesCheckboxRight.clicked.connect(
            self.annotOptionClickedRight
        )

        for filtersDict in self.filtersWins.values():
            filtersDict['action'].toggled.connect(self.filterToggled)
        
        self.segmentToolAction.triggered.connect(self.segmentToolActionTriggered)

        self.addDelRoiAction.triggered.connect(self.addDelROI)
        self.addDelPolyLineRoiAction.toggled.connect(self.addDelPolyLineRoi_cb)
        self.delBorderObjAction.triggered.connect(self.delBorderObj)
        
        self.brushAutoFillCheckbox.toggled.connect(self.brushAutoFillToggled)
        self.brushAutoHideCheckbox.toggled.connect(self.brushAutoHideToggled)

        self.imgGrad.sigLookupTableChanged.connect(self.imgGradLUT_cb)
        self.imgGrad.gradient.sigGradientChangeFinished.connect(
            self.imgGradLUTfinished_cb
        )

        self.normalizeQActionGroup.triggered.connect(
            self.normaliseIntensitiesActionTriggered
        )
        self.imgPropertiesAction.triggered.connect(self.editImgProperties)

        self.relabelSequentialAction.triggered.connect(
            self.relabelSequentialCallback
        )

        self.zoomToObjsAction.triggered.connect(self.zoomToObjsActionCallback)
        self.zoomOutAction.triggered.connect(self.zoomOut)

        self.viewCcaTableAction.triggered.connect(self.viewCcaTable)

        self.guiTabControl.propsQGBox.idSB.valueChanged.connect(
            self.updatePropsWidget
        )
        self.guiTabControl.highlightCheckbox.toggled.connect(
            self.highlightIDcheckBoxToggled
        )
        intensMeasurQGBox = self.guiTabControl.intensMeasurQGBox
        intensMeasurQGBox.additionalMeasCombobox.currentTextChanged.connect(
            self.updatePropsWidget
        )
        intensMeasurQGBox.channelCombobox.currentTextChanged.connect(
            self.updatePropsWidget
        )
        
        propsQGBox = self.guiTabControl.propsQGBox
        propsQGBox.additionalPropsCombobox.currentTextChanged.connect(
            self.updatePropsWidget
        )

    def gui_createShowPropsButton(self, side='left'):
        self.leftSideDocksLayout = QVBoxLayout()            
        self.leftSideDocksLayout.setSpacing(0)
        self.leftSideDocksLayout.setContentsMargins(0,0,0,0)
        self.rightSideDocksLayout = QVBoxLayout()            
        self.rightSideDocksLayout.setSpacing(0)
        self.rightSideDocksLayout.setContentsMargins(0,0,0,0)
        self.showPropsDockButton = widgets.expandCollapseButton()
        self.showPropsDockButton.setDisabled(True)
        self.showPropsDockButton.setFocusPolicy(Qt.NoFocus)
        self.showPropsDockButton.setToolTip('Show object properties')
        if side == 'left':
            self.leftSideDocksLayout.addWidget(self.showPropsDockButton)
        else:
            self.rightSideDocksLayout.addWidget(self.showPropsDockButton)
            
    def gui_createQuickSettingsWidgets(self):
        self.quickSettingsLayout = QVBoxLayout()
        self.quickSettingsGroupbox = widgets.GroupBox()
        self.quickSettingsGroupbox.setTitle('Quick settings')

        layout = QFormLayout()
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        layout.setFormAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.autoSaveToggle = widgets.Toggle()
        autoSaveTooltip = (
            'Automatically store a copy of the segmentation data and of '
            'the annotations in the `.recovery` folder after every edit.'
        )
        self.autoSaveToggle.setChecked(True)
        self.autoSaveToggle.setToolTip(autoSaveTooltip)
        autoSaveLabel = QLabel('Autosave segm.')
        autoSaveLabel.setToolTip(autoSaveTooltip)
        layout.addRow(autoSaveLabel, self.autoSaveToggle)
        
        self.annotLostObjsToggle = widgets.Toggle()
        annotLostObjsToggleTooltip = (
            'Automatically store a copy of the segmentation data and of '
            'the annotations in the `.recovery` folder after every edit.'
        )
        self.annotLostObjsToggle.setChecked(True)
        self.annotLostObjsToggle.setToolTip(annotLostObjsToggleTooltip)
        label = QLabel('Annotate lost objects')
        label.setToolTip(annotLostObjsToggleTooltip)
        layout.addRow(label, self.annotLostObjsToggle)

        self.highLowResToggle = widgets.Toggle()
        self.widgetsWithShortcut['High resolution'] = self.highLowResToggle
        self.highLowResToggle.setShortcut('w')
        highLowResTooltip = (
            'Resolution of the text annotations. High resolution results '
            'in slower update of the annotations.\n'
            'Not recommended with a number of segmented objects > 500.\n\n'
            'SHORTCUT: "W" key'
        )
        highResLabel = QLabel('High resolution')
        highResLabel.setToolTip(highLowResTooltip)
        self.highLowResToggle.setToolTip(highLowResTooltip)
        layout.addRow(highResLabel, self.highLowResToggle)

        self.realTimeTrackingToggle = widgets.Toggle()
        self.realTimeTrackingToggle.setChecked(True)
        self.realTimeTrackingToggle.setDisabled(True)
        label = QLabel('Real-time tracking')
        label.setDisabled(True)
        self.realTimeTrackingToggle.label = label
        layout.addRow(label, self.realTimeTrackingToggle)

        self.pxModeToggle = widgets.Toggle()
        self.pxModeToggle.setChecked(True)
        pxModeTooltip = (
            'With "Pixel mode" active, the text annotations scales relative '
            'to the object when zooming in/out (fixed size in pixels).\n'
            'This is typically faster to render, but it makes annotations '
            'smaller/larger when zooming in/out, respectively.\n\n'
            'Try activating it to speed up the annotation of many objects '
            'in high resolution mode.\n\n'
            'After activating it, you might need to increase the font size '
            'from the menu on the top menubar `Edit --> Font size`.'
        )
        pxModeLabel = QLabel('Pixel mode')
        self.pxModeToggle.label = pxModeLabel
        pxModeLabel.setToolTip(pxModeTooltip)
        self.pxModeToggle.setToolTip(pxModeTooltip)
        self.pxModeToggle.clicked.connect(self.pxModeToggled)
        layout.addRow(pxModeLabel, self.pxModeToggle)

        # Font size
        self.fontSizeSpinBox = widgets.SpinBox()
        self.fontSizeSpinBox.setMinimum(1)
        self.fontSizeSpinBox.setMaximum(99)
        layout.addRow('Font size', self.fontSizeSpinBox) 
        savedFontSize = str(self.df_settings.at['fontSize', 'value'])
        if savedFontSize.find('pt') != -1:
            savedFontSize = savedFontSize[:-2]
        self.fontSize = int(savedFontSize)
        if 'pxMode' not in self.df_settings.index:
            # Users before introduction of pxMode had pxMode=False, but now 
            # the new default is True. This requires larger font size.
            self.fontSize = 2*self.fontSize
            self.df_settings.at['pxMode', 'value'] = 1
            self.df_settings.to_csv(settings_csv_path)
        self.fontSizeSpinBox.setValue(self.fontSize)
        self.fontSizeSpinBox.editingFinished.connect(self.changeFontSize) 
        self.fontSizeSpinBox.sigUpClicked.connect(self.changeFontSize)
        self.fontSizeSpinBox.sigDownClicked.connect(self.changeFontSize)

        self.quickSettingsGroupbox.setLayout(layout)
        self.quickSettingsLayout.addWidget(self.quickSettingsGroupbox)
        self.quickSettingsLayout.addStretch(1)

    def gui_createImg1Widgets(self):
        # Toggle contours/ID combobox
        self.drawIDsContComboBoxSegmItems = [
            'Draw IDs and contours',
            'Draw IDs and overlay segm. masks',
            'Draw only cell cycle info',
            'Draw cell cycle info and contours',
            'Draw cell cycle info and overlay segm. masks',
            'Draw only mother-bud lines',
            'Draw only IDs',
            'Draw only contours',
            'Draw only overlay segm. masks',
            'Draw nothing'
        ]
        self.drawIDsContComboBox = QComboBox()
        self.drawIDsContComboBox.setFont(_font)
        self.drawIDsContComboBox.addItems(self.drawIDsContComboBoxSegmItems)
        self.drawIDsContComboBox.setVisible(False)

        self.annotIDsCheckbox = widgets.CheckBox(
            'IDs', keyPressCallback=self.resetFocus)
        self.annotCcaInfoCheckbox = widgets.CheckBox(
            'Cell cycle info', keyPressCallback=self.resetFocus)
        self.annotNumZslicesCheckbox = widgets.CheckBox(
            'No. z-slices/object', keyPressCallback=self.resetFocus)

        self.annotContourCheckbox = widgets.CheckBox(
            'Contours', keyPressCallback=self.resetFocus)
        self.annotSegmMasksCheckbox = widgets.CheckBox(
            'Segm. masks', keyPressCallback=self.resetFocus)

        self.drawMothBudLinesCheckbox = widgets.CheckBox(
            'Only mother-daughter line', keyPressCallback=self.resetFocus
        )

        self.drawNothingCheckbox = widgets.CheckBox(
            'Do not annotate', keyPressCallback=self.resetFocus
        )

        self.annotOptionsWidget = QWidget()
        annotOptionsLayout = QHBoxLayout()

        # Show tree info checkbox
        self.showTreeInfoCheckbox = widgets.CheckBox(
            'Show tree info', keyPressCallback=self.resetFocus
        )
        self.showTreeInfoCheckbox.setFont(_font)
        sp = self.showTreeInfoCheckbox.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.showTreeInfoCheckbox.setSizePolicy(sp)
        self.showTreeInfoCheckbox.hide()

        annotOptionsLayout.addWidget(self.showTreeInfoCheckbox)
        annotOptionsLayout.addWidget(QLabel(' | '))
        annotOptionsLayout.addWidget(self.annotIDsCheckbox)
        annotOptionsLayout.addWidget(self.annotCcaInfoCheckbox)
        annotOptionsLayout.addWidget(self.drawMothBudLinesCheckbox)
        annotOptionsLayout.addWidget(self.annotNumZslicesCheckbox)
        annotOptionsLayout.addWidget(QLabel(' | '))
        annotOptionsLayout.addWidget(self.annotContourCheckbox)
        annotOptionsLayout.addWidget(self.annotSegmMasksCheckbox)
        annotOptionsLayout.addWidget(QLabel(' | '))
        annotOptionsLayout.addWidget(self.drawNothingCheckbox)
        annotOptionsLayout.addWidget(self.drawIDsContComboBox)
        self.annotOptionsLayout = annotOptionsLayout

        # Toggle highlight z+-1 objects combobox
        self.highlightZneighObjCheckbox = widgets.CheckBox(
            'Highlight objects in neighbouring z-slices', 
            keyPressCallback=self.resetFocus
        )
        self.highlightZneighObjCheckbox.setFont(_font)
        self.highlightZneighObjCheckbox.hide()

        annotOptionsLayout.addWidget(self.highlightZneighObjCheckbox)
        self.annotOptionsWidget.setLayout(annotOptionsLayout)

        # Annotations options right image
        self.annotIDsCheckboxRight = widgets.CheckBox(
            'IDs', keyPressCallback=self.resetFocus)
        self.annotCcaInfoCheckboxRight = widgets.CheckBox(
            'Cell cycle info', keyPressCallback=self.resetFocus)
        self.annotNumZslicesCheckboxRight = widgets.CheckBox(
            'No. z-slices/object', keyPressCallback=self.resetFocus
        )

        self.annotContourCheckboxRight = widgets.CheckBox(
            'Contours', keyPressCallback=self.resetFocus)
        self.annotSegmMasksCheckboxRight = widgets.CheckBox(
            'Segm. masks', keyPressCallback=self.resetFocus)

        self.drawMothBudLinesCheckboxRight = widgets.CheckBox(
            'Only mother-daughter line', keyPressCallback=self.resetFocus
        )

        self.drawNothingCheckboxRight = widgets.CheckBox(
            'Do not annotate', keyPressCallback=self.resetFocus)

        self.annotOptionsWidgetRight = QWidget()
        annotOptionsLayoutRight = QHBoxLayout()

        annotOptionsLayoutRight.addWidget(QLabel('       '))
        annotOptionsLayoutRight.addWidget(QLabel(' | '))
        annotOptionsLayoutRight.addWidget(self.annotIDsCheckboxRight)
        annotOptionsLayoutRight.addWidget(self.annotCcaInfoCheckboxRight)
        annotOptionsLayoutRight.addWidget(self.drawMothBudLinesCheckboxRight)
        annotOptionsLayoutRight.addWidget(self.annotNumZslicesCheckboxRight)
        annotOptionsLayoutRight.addWidget(QLabel(' | '))
        annotOptionsLayoutRight.addWidget(self.annotContourCheckboxRight)
        annotOptionsLayoutRight.addWidget(self.annotSegmMasksCheckboxRight)
        annotOptionsLayoutRight.addWidget(QLabel(' | '))
        annotOptionsLayoutRight.addWidget(self.drawNothingCheckboxRight)
        self.annotOptionsLayoutRight = annotOptionsLayoutRight
        
        self.annotOptionsWidgetRight.setLayout(annotOptionsLayoutRight)

        # Frames scrollbar
        self.navigateScrollBar = widgets.navigateQScrollBar(Qt.Horizontal)
        self.navigateScrollBar.setDisabled(True)
        self.navigateScrollBar.setMinimum(1)
        self.navigateScrollBar.setMaximum(1)
        self.navigateScrollBar.setToolTip(
            'NOTE: The maximum frame number that can be visualized with this '
            'scrollbar\n'
            'is the last visited frame with the selected mode\n'
            '(see "Mode" selector on the top-right).\n\n'
            'If the scrollbar does not move it means that you never visited\n'
            'any frame with current mode.\n\n'
            'Note that the "Viewer" mode allows you to scroll ALL frames.'
        )
        t_label = QLabel('frame n.  ')
        t_label.setFont(_font)
        self.t_label = t_label

        # z-slice scrollbars
        self.zSliceScrollBar = widgets.linkedQScrollbar(Qt.Horizontal)

        self.zProjComboBox = QComboBox()
        self.zProjComboBox.setFont(_font)
        self.zProjComboBox.addItems([
            'single z-slice',
            'max z-projection',
            'mean z-projection',
            'median z-proj.'
        ])

        self.zSliceOverlay_SB = widgets.ScrollBar(Qt.Horizontal)
        _z_label = QLabel('Overlay z-slice  ')
        _z_label.setFont(_font)
        _z_label.setDisabled(True)
        self.overlay_z_label = _z_label

        self.zProjOverlay_CB = QComboBox()
        self.zProjOverlay_CB.setFont(_font)
        self.zProjOverlay_CB.addItems([
            'single z-slice', 'max z-projection', 'mean z-projection',
            'median z-proj.', 'same as above'
        ])
        self.zSliceOverlay_SB.setDisabled(True)

        self.img1BottomGroupbox = self.gui_getImg1BottomWidgets()

    def gui_getImg1BottomWidgets(self):
        bottomLeftLayout = QGridLayout()
        self.bottomLeftLayout = bottomLeftLayout
        container = QGroupBox('Navigate and annotate left image')

        row = 0
        bottomLeftLayout.addWidget(self.annotOptionsWidget, row, 0, 1, 4)
        # bottomLeftLayout.addWidget(
        #     self.drawIDsContComboBox, row, 1, 1, 2,
        #     alignment=Qt.AlignCenter
        # )

        # bottomLeftLayout.addWidget(
        #     self.showTreeInfoCheckbox, row, 0, 1, 1,
        #     alignment=Qt.AlignCenter
        # )

        row += 1
        navWidgetsLayout = QHBoxLayout()
        self.navSpinBox = widgets.SpinBox(disableKeyPress=True)
        self.navSpinBox.setMinimum(1)
        self.navSpinBox.setMaximum(100)
        self.navSizeLabel = QLabel('/ND')
        navWidgetsLayout.addWidget(self.t_label)
        navWidgetsLayout.addWidget(self.navSpinBox)
        navWidgetsLayout.addWidget(self.navSizeLabel)
        bottomLeftLayout.addLayout(
            navWidgetsLayout, row, 0, alignment=Qt.AlignRight
        )
        bottomLeftLayout.addWidget(self.navigateScrollBar, row, 1, 1, 2)
        sp = self.navigateScrollBar.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.navigateScrollBar.setSizePolicy(sp)
        self.navSpinBox.connectValueChanged(self.navigateSpinboxValueChanged)
        self.navSpinBox.editingFinished.connect(self.navigateSpinboxEditingFinished)

        row += 1
        zSliceCheckboxLayout = QHBoxLayout()
        self.zSliceCheckbox = QCheckBox('z-slice')
        self.zSliceSpinbox = widgets.SpinBox(disableKeyPress=True)
        self.zSliceSpinbox.setMinimum(1)
        self.SizeZlabel = QLabel('/ND')
        self.zSliceCheckbox.setToolTip(
            'Activate/deactivate control of the z-slices with keyboard arrows.\n\n'
            'SHORTCUT to toggle ON/OFF: "Z" key'
        )
        zSliceCheckboxLayout.addWidget(self.zSliceCheckbox)
        zSliceCheckboxLayout.addWidget(self.zSliceSpinbox)
        zSliceCheckboxLayout.addWidget(self.SizeZlabel)
        bottomLeftLayout.addLayout(
            zSliceCheckboxLayout, row, 0, alignment=Qt.AlignRight
        )
        bottomLeftLayout.addWidget(self.zSliceScrollBar, row, 1, 1, 2)
        bottomLeftLayout.addWidget(self.zProjComboBox, row, 3)
        self.zSliceSpinbox.connectValueChanged(self.onZsliceSpinboxValueChange)
        self.zSliceSpinbox.editingFinished.connect(self.zSliceScrollBarReleased)

        row += 1
        bottomLeftLayout.addWidget(
            self.overlay_z_label, row, 0, alignment=Qt.AlignRight
        )
        bottomLeftLayout.addWidget(self.zSliceOverlay_SB, row, 1, 1, 2)

        bottomLeftLayout.addWidget(self.zProjOverlay_CB, row, 3)

        row += 1
        self.alphaScrollbarRow = row

        bottomLeftLayout.setColumnStretch(0,0)
        bottomLeftLayout.setColumnStretch(1,3)
        bottomLeftLayout.setColumnStretch(2,0)

        container.setLayout(bottomLeftLayout)
        return container

    def gui_createLabWidgets(self):
        bottomRightLayout = QVBoxLayout()
        self.rightBottomGroupbox = widgets.GroupBox(
            'Annotate right image', keyPressCallback=self.resetFocus)
        self.rightBottomGroupbox.setCheckable(True)
        self.rightBottomGroupbox.setChecked(False)
        self.rightBottomGroupbox.hide()

        self.annotateRightHowCombobox = QComboBox()
        self.annotateRightHowCombobox.setFont(_font)
        self.annotateRightHowCombobox.addItems(self.drawIDsContComboBoxSegmItems)
        self.annotateRightHowCombobox.setCurrentIndex(
            self.drawIDsContComboBox.currentIndex()
        )
        self.annotateRightHowCombobox.setVisible(False)

        self.annotOptionsLayoutRight.addWidget(self.annotateRightHowCombobox)

        bottomRightLayout.addWidget(self.annotOptionsWidgetRight)
        bottomRightLayout.addStretch(1)

        self.rightBottomGroupbox.setLayout(bottomRightLayout)

        self.rightBottomGroupbox.toggled.connect(self.rightImageControlsToggled)

    def rightImageControlsToggled(self, checked):
        if self.isDataLoading:
            return
        self.updateAllImages()
    
    def setFocusGraphics(self):
        self.graphLayout.setFocus()
    
    def setFocusMain(self):
        self.setFocus()
    
    def resetFocus(self):
        self.setFocusGraphics()
        self.setFocusMain()

    def gui_createBottomWidgetsToBottomLayout(self):
        # self.bottomDockWidget = QDockWidget(self)
        bottomScrollArea = widgets.ScrollArea(resizeVerticalOnShow=True)
        bottomScrollArea.sigLeaveEvent.connect(self.setFocusMain)
        bottomWidget = QWidget()
        bottomScrollAreaLayout = QVBoxLayout()
        self.bottomLayout = QHBoxLayout()
        self.bottomLayout.addLayout(self.quickSettingsLayout)
        self.bottomLayout.addStretch(1)
        self.bottomLayout.addWidget(self.img1BottomGroupbox)
        self.bottomLayout.addStretch(1)
        self.bottomLayout.addWidget(self.rightBottomGroupbox)
        self.bottomLayout.addStretch(1)   

        bottomScrollAreaLayout.addLayout(self.bottomLayout)
        bottomScrollAreaLayout.addStretch(1)

        bottomWidget.setLayout(bottomScrollAreaLayout)
        bottomScrollArea.setWidgetResizable(True)
        bottomScrollArea.setWidget(bottomWidget)
        self.bottomScrollArea = bottomScrollArea
        
        if 'bottom_sliders_zoom_perc' in self.df_settings.index:
            val = int(self.df_settings.at['bottom_sliders_zoom_perc', 'value'])
            zoom_perc = val
        else:
            zoom_perc = 100
        self.bottomLayoutContextMenu = QMenu('Bottom layout', self)
        zoomMenu = self.bottomLayoutContextMenu.addMenu('Zoom')
        actions = []
        self.bottomLayoutContextMenu.zoomActionGroup = QActionGroup(zoomMenu)
        for perc in np.arange(50, 151, 10):
            action = QAction(f'{perc}%', zoomMenu)
            action.setCheckable(True)
            if perc == zoom_perc:
                action.setChecked(True)
            action.toggled.connect(self.zoomBottomLayoutActionTriggered)
            actions.append(action)
            self.bottomLayoutContextMenu.zoomActionGroup.addAction(action)
        zoomMenu.addActions(actions)
        resetAction = self.bottomLayoutContextMenu.addAction(
            'Reset default height'
        )
        resetAction.triggered.connect(self.resizeGui)
        retainSpaceAction = self.bottomLayoutContextMenu.addAction(
            'Retain space of hidden sliders'
        )
        retainSpaceAction.setCheckable(True)
        if 'retain_space_hidden_sliders' in self.df_settings.index:
            retainSpaceChecked = (
                self.df_settings.at['retain_space_hidden_sliders', 'value']
                == 'Yes'
            )
        else:
            retainSpaceChecked = True
        retainSpaceAction.setChecked(retainSpaceChecked)
        retainSpaceAction.toggled.connect(self.retainSpaceSlidersToggled)
        self.retainSpaceSlidersAction = retainSpaceAction
        self.setBottomLayoutStretch()
    
    def gui_resetBottomLayoutHeight(self):
        self.h = self.defaultWidgetHeightBottomLayout
        self.checkBoxesHeight = 14
        self.fontPixelSize = 11
        self.resizeSlidersArea()

    def gui_createGraphicsPlots(self):
        self.graphLayout = pg.GraphicsLayoutWidget()
        if self.invertBwAction.isChecked():
            self.graphLayout.setBackground(graphLayoutBkgrColor)
            self.titleColor = 'black'
        else:
            self.graphLayout.setBackground(darkBkgrColor)
            self.titleColor = 'white'

        self.lutItemsLayout = self.graphLayout.addLayout(row=1, col=0)
        # self.lutItemsLayout.setBorder('w')

        # Left plot
        self.ax1 = widgets.MainPlotItem(showWelcomeText=True)
        self.ax1.invertY(True)
        self.ax1.setAspectLocked(True)
        self.ax1.hideAxis('bottom')
        self.ax1.hideAxis('left')
        self.plotsCol = 1
        self.graphLayout.addItem(self.ax1, row=1, col=1)

        # Right plot
        self.ax2 = widgets.MainPlotItem()
        self.ax2.setAspectLocked(True)
        self.ax2.invertY(True)
        self.ax2.hideAxis('bottom')
        self.ax2.hideAxis('left')
        self.graphLayout.addItem(self.ax2, row=1, col=2)

    def gui_addGraphicsItems(self):
        # Auto image adjustment button
        proxy = QGraphicsProxyWidget()
        equalizeHistPushButton = QPushButton("Auto-contrast")
        widthHint = equalizeHistPushButton.sizeHint().width()
        equalizeHistPushButton.setMaximumWidth(widthHint)
        equalizeHistPushButton.setCheckable(True)
        if not self.invertBwAction.isChecked():
            equalizeHistPushButton.setStyleSheet(
                'QPushButton {background-color: #282828; color: #F0F0F0;}'
            )
        self.equalizeHistPushButton = equalizeHistPushButton
        proxy.setWidget(equalizeHistPushButton)
        self.graphLayout.addItem(proxy, row=0, col=0)
        self.equalizeHistPushButton = equalizeHistPushButton

        # Left image histogram
        self.imgGrad = widgets.myHistogramLUTitem(parent=self, name='image')
        self.imgGrad.restoreState(self.df_settings)
        self.lutItemsLayout.addItem(self.imgGrad, row=0, col=0)

        # Colormap gradient widget
        self.labelsGrad = widgets.labelsGradientWidget(parent=self)
        try:
            stateFound = self.labelsGrad.restoreState(self.df_settings)
        except Exception as e:
            self.logger.exception(traceback.format_exc())
            print('======================================')
            self.logger.info(
                'Failed to restore previously used colormap. '
                'Using default colormap "viridis"'
            )
            self.labelsGrad.item.loadPreset('viridis')
        
        # Add actions to imgGrad gradient item
        self.imgGrad.gradient.menu.addAction(
            self.labelsGrad.showLabelsImgAction
        )
        self.imgGrad.gradient.menu.addAction(
            self.labelsGrad.showRightImgAction
        )
        
        # Add actions to view menu
        self.viewMenu.addAction(self.labelsGrad.showLabelsImgAction)
        self.viewMenu.addAction(self.labelsGrad.showRightImgAction)
        
        # Right image histogram
        self.imgGradRight = widgets.baseHistogramLUTitem(
            name='image', parent=self, gradientPosition='left'
        )
        self.imgGradRight.gradient.menu.addAction(
            self.labelsGrad.showLabelsImgAction
        )
        self.imgGradRight.gradient.menu.addAction(
            self.labelsGrad.showRightImgAction
        )

        # Title
        self.titleLabel = pg.LabelItem(
            justify='center', color=self.titleColor, size='14pt'
        )
        self.graphLayout.addItem(self.titleLabel, row=0, col=1, colspan=2)

    def gui_createTextAnnotColors(self, r, g, b, custom=False):
        if custom:
            self.objLabelAnnotRgb = (int(r), int(g), int(b))
            self.SphaseAnnotRgb = (int(r*0.9), int(r*0.9), int(b*0.9))
            self.G1phaseAnnotRgba = (int(r*0.8), int(g*0.8), int(b*0.8), 220)
        else:
            self.objLabelAnnotRgb = (255, 255, 255) # white
            self.SphaseAnnotRgb = (229, 229, 229)
            self.G1phaseAnnotRgba = (204, 204, 204, 220)
        self.dividedAnnotRgb = (245, 188, 1) # orange

        self.emptyBrush = pg.mkBrush((0,0,0,0))
        self.emptyPen = pg.mkPen((0,0,0,0))
    
    def gui_setTextAnnotColors(self):
        self.textAnnot[0].setColors(
            self.objLabelAnnotRgb, self.dividedAnnotRgb, self.SphaseAnnotRgb,
            self.G1phaseAnnotRgba, self.objLostAnnotRgb
        )

        self.textAnnot[1].setColors(
            self.objLabelAnnotRgb, self.dividedAnnotRgb, self.SphaseAnnotRgb,
            self.G1phaseAnnotRgba, self.objLostAnnotRgb
        )


    def gui_createPlotItems(self):
        if 'textIDsColor' in self.df_settings.index:
            rgbString = self.df_settings.at['textIDsColor', 'value']
            r, g, b = colors.rgb_str_to_values(rgbString)
            self.gui_createTextAnnotColors(r, g, b, custom=True)
            self.textIDsColorButton.setColor((r, g, b))
        else:
            self.gui_createTextAnnotColors(0,0,0, custom=False)

        if 'labels_text_color' in self.df_settings.index:
            rgbString = self.df_settings.at['labels_text_color', 'value']
            r, g, b = colors.rgb_str_to_values(rgbString)
            self.ax2_textColor = (r, g, b)
        else:
            self.ax2_textColor = (255, 0, 0)
        
        self.emptyLab = np.zeros((2,2), dtype=np.uint8)

        # Right image item linked to left
        self.rightImageItem = pg.ImageItem()
        self.imgGradRight.setImageItem(self.rightImageItem)   
        self.ax2.addItem(self.rightImageItem)
        
        # Left image
        self.img1 = widgets.ParentImageItem(
            linkedImageItem=self.rightImageItem,
            activatingAction=self.labelsGrad.showRightImgAction
        )
        self.imgGrad.setImageItem(self.img1)
        self.ax1.addItem(self.img1)

        # Right image
        self.img2 = widgets.labImageItem()
        self.ax2.addItem(self.img2)

        self.topLayerItems = []
        self.topLayerItemsRight = []

        self.gui_createContourPens()
        self.gui_createMothBudLinePens()

        self.eraserCirclePen = pg.mkPen(width=1.5, color='r')
        
        # Temporary line item connecting bud to new mother
        self.BudMothTempLine = pg.PlotDataItem(pen=self.NewBudMoth_Pen)
        self.topLayerItems.append(self.BudMothTempLine)

        # Overlay segm. masks item
        self.labelsLayerImg1 = widgets.BaseImageItem()
        self.ax1.addItem(self.labelsLayerImg1)

        self.labelsLayerRightImg = widgets.BaseImageItem()
        self.ax2.addItem(self.labelsLayerRightImg)

        # Red/green border rect item
        self.GreenLinePen = pg.mkPen(color='g', width=2)
        self.RedLinePen = pg.mkPen(color='r', width=2)
        self.ax1BorderLine = pg.PlotDataItem()
        self.topLayerItems.append(self.ax1BorderLine)
        self.ax2BorderLine = pg.PlotDataItem(pen=pg.mkPen(color='r', width=2))
        self.topLayerItems.append(self.ax2BorderLine)

        # Brush/Eraser/Wand.. layer item
        self.tempLayerRightImage = pg.ImageItem()
        # self.tempLayerImg1 = pg.ImageItem()
        self.tempLayerImg1 = widgets.ParentImageItem(
            linkedImageItem=self.tempLayerRightImage,
            activatingAction=self.labelsGrad.showRightImgAction
        )
        self.topLayerItems.append(self.tempLayerImg1)
        self.topLayerItemsRight.append(self.tempLayerRightImage)

        # Highlighted ID layer items
        self.highLightIDLayerImg1 = pg.ImageItem()
        self.topLayerItems.append(self.highLightIDLayerImg1)  

        # Highlighted ID layer items
        self.highLightIDLayerRightImage = pg.ImageItem()
        self.topLayerItemsRight.append(self.highLightIDLayerRightImage)

        # Keep IDs temp layers
        self.keepIDsTempLayerRight = pg.ImageItem()
        self.keepIDsTempLayerLeft = widgets.ParentImageItem(
            linkedImageItem=self.keepIDsTempLayerRight,
            activatingAction=self.labelsGrad.showRightImgAction
        )
        self.topLayerItems.append(self.keepIDsTempLayerLeft)
        self.topLayerItemsRight.append(self.keepIDsTempLayerRight)

        # Searched ID contour
        self.searchedIDitemRight = pg.ScatterPlotItem()
        self.searchedIDitemRight.setData(
            [], [], symbol='s', pxMode=False, size=1,
            brush=pg.mkBrush(color=(255,0,0,150)),
            pen=pg.mkPen(width=2, color='r'), tip=None
        )
        self.searchedIDitemLeft = pg.ScatterPlotItem()
        self.searchedIDitemLeft.setData(
            [], [], symbol='s', pxMode=False, size=1,
            brush=pg.mkBrush(color=(255,0,0,150)),
            pen=pg.mkPen(width=2, color='r'), tip=None
        )
        self.topLayerItems.append(self.searchedIDitemLeft)
        self.topLayerItemsRight.append(self.searchedIDitemRight)

        
        # Brush circle img1
        self.ax1_BrushCircle = pg.ScatterPlotItem()
        self.ax1_BrushCircle.setData(
            [], [], symbol='o', pxMode=False,
            brush=pg.mkBrush((255,255,255,50)),
            pen=pg.mkPen(width=2), tip=None
        )
        self.topLayerItems.append(self.ax1_BrushCircle)

        # Eraser circle img1
        self.ax1_EraserCircle = pg.ScatterPlotItem()
        self.ax1_EraserCircle.setData(
            [], [], symbol='o', pxMode=False,
            brush=None, pen=self.eraserCirclePen, tip=None
        )
        self.topLayerItems.append(self.ax1_EraserCircle)

        self.ax1_EraserX = pg.ScatterPlotItem()
        self.ax1_EraserX.setData(
            [], [], symbol='x', pxMode=False, size=3,
            brush=pg.mkBrush(color=(255,0,0,50)),
            pen=pg.mkPen(width=1, color='r'), tip=None
        )
        self.topLayerItems.append(self.ax1_EraserX)

        # Brush circle img1
        self.labelRoiCircItemLeft = widgets.LabelRoiCircularItem()
        self.labelRoiCircItemLeft.cleared = False
        self.labelRoiCircItemLeft.setData(
            [], [], symbol='o', pxMode=False,
            brush=pg.mkBrush(color=(255,0,0,0)),
            pen=pg.mkPen(color='r', width=2), tip=None
        )
        self.labelRoiCircItemRight = widgets.LabelRoiCircularItem()
        self.labelRoiCircItemRight.cleared = False
        self.labelRoiCircItemRight.setData(
            [], [], symbol='o', pxMode=False,
            brush=pg.mkBrush(color=(255,0,0,0)),
            pen=pg.mkPen(color='r', width=2), tip=None
        )
        self.topLayerItems.append(self.labelRoiCircItemLeft)
        self.topLayerItemsRight.append(self.labelRoiCircItemRight)
        
        self.ax1_binnedIDs_ScatterPlot = widgets.BaseScatterPlotItem()
        self.ax1_binnedIDs_ScatterPlot.setData(
            [], [], symbol='t', pxMode=False,
            brush=pg.mkBrush((255,0,0,50)), size=15,
            pen=pg.mkPen(width=3, color='r'), tip=None
        )
        self.topLayerItems.append(self.ax1_binnedIDs_ScatterPlot)
        
        self.ax1_ripIDs_ScatterPlot = widgets.BaseScatterPlotItem()
        self.ax1_ripIDs_ScatterPlot.setData(
            [], [], symbol='x', pxMode=False,
            brush=pg.mkBrush((255,0,0,50)), size=15,
            pen=pg.mkPen(width=2, color='r'), tip=None
        )
        self.topLayerItems.append(self.ax1_ripIDs_ScatterPlot)

        # Ruler plotItem and scatterItem
        rulerPen = pg.mkPen(color='r', style=Qt.DashLine, width=2)
        self.ax1_rulerPlotItem = pg.PlotDataItem(pen=rulerPen)
        self.ax1_rulerAnchorsItem = pg.ScatterPlotItem(
            symbol='o', size=9,
            brush=pg.mkBrush((255,0,0,50)),
            pen=pg.mkPen((255,0,0), width=2), tip=None
        )
        self.topLayerItems.append(self.ax1_rulerPlotItem)
        self.topLayerItems.append(self.ax1_rulerAnchorsItem)

        # Start point of polyline roi
        self.ax1_point_ScatterPlot = pg.ScatterPlotItem()
        self.ax1_point_ScatterPlot.setData(
            [], [], symbol='o', pxMode=False, size=3,
            pen=pg.mkPen(width=2, color='r'),
            brush=pg.mkBrush((255,0,0,50)), tip=None
        )
        self.topLayerItems.append(self.ax1_point_ScatterPlot)

        # Experimental: scatter plot to add a point marker
        self.startPointPolyLineItem = pg.ScatterPlotItem()
        self.startPointPolyLineItem.setData(
            [], [], symbol='o', size=9,
            pen=pg.mkPen(width=2, color='r'),
            brush=pg.mkBrush((255,0,0,50)),
            hoverable=True, hoverBrush=pg.mkBrush((255,0,0,255)), tip=None
        )
        self.topLayerItems.append(self.startPointPolyLineItem)

        # Eraser circle img2
        self.ax2_EraserCircle = pg.ScatterPlotItem()
        self.ax2_EraserCircle.setData(
            [], [], symbol='o', pxMode=False, brush=None,
            pen=self.eraserCirclePen, tip=None
        )
        self.ax2.addItem(self.ax2_EraserCircle)
        self.ax2_EraserX = pg.ScatterPlotItem()
        self.ax2_EraserX.setData([], [], symbol='x', pxMode=False, size=3,
                                      brush=pg.mkBrush(color=(255,0,0,50)),
                                      pen=pg.mkPen(width=1.5, color='r'))
        self.ax2.addItem(self.ax2_EraserX)

        # Brush circle img2
        self.ax2_BrushCirclePen = pg.mkPen(width=2)
        self.ax2_BrushCircleBrush = pg.mkBrush((255,255,255,50))
        self.ax2_BrushCircle = pg.ScatterPlotItem()
        self.ax2_BrushCircle.setData(
            [], [], symbol='o', pxMode=False,
            brush=self.ax2_BrushCircleBrush,
            pen=self.ax2_BrushCirclePen, tip=None
        )
        self.ax2.addItem(self.ax2_BrushCircle)

        # Random walker markers colors
        self.RWbkgrColor = (255,255,0)
        self.RWforegrColor = (124,5,161)


        # # Experimental: brush cursors
        # self.eraserCursor = QCursor(QIcon(":eraser.svg").pixmap(30, 30))
        # brushCursorPixmap = QIcon(":brush-cursor.png").pixmap(32, 32)
        # self.brushCursor = QCursor(brushCursorPixmap, 16, 16)

        # Annotated metadata markers (ScatterPlotItem)
        self.ax2_binnedIDs_ScatterPlot = widgets.BaseScatterPlotItem()
        self.ax2_binnedIDs_ScatterPlot.setData(
            [], [], symbol='t', pxMode=False,
            brush=pg.mkBrush((255,0,0,50)), size=15,
            pen=pg.mkPen(width=3, color='r'), tip=None
        )
        self.ax2.addItem(self.ax2_binnedIDs_ScatterPlot)
        
        self.ax2_ripIDs_ScatterPlot = widgets.BaseScatterPlotItem()
        self.ax2_ripIDs_ScatterPlot.setData(
            [], [], symbol='x', pxMode=False,
            brush=pg.mkBrush((255,0,0,50)), size=15,
            pen=pg.mkPen(width=2, color='r'), tip=None
        )
        self.ax2.addItem(self.ax2_ripIDs_ScatterPlot)

        self.freeRoiItem = widgets.PlotCurveItem(
            pen=pg.mkPen(color='r', width=2)
        )
        self.topLayerItems.append(self.freeRoiItem)

        self.ghostContourItemLeft = widgets.GhostContourItem()
        self.ghostContourItemRight = widgets.GhostContourItem()

        self.ghostMaskItemLeft = widgets.GhostMaskItem()
        self.ghostMaskItemRight = widgets.GhostMaskItem()
    
    def gui_createLabelRoiItem(self):
        Y, X = self.currentLab2D.shape
        # Label ROI rectangle
        pen = pg.mkPen('r', width=3)
        self.labelRoiItem = widgets.ROI(
            (0,0), (0,0),
            maxBounds=QRectF(QRect(0,0,X,Y)),
            scaleSnap=True,
            translateSnap=True,
            pen=pen, hoverPen=pen
        )

        posData = self.data[self.pos_i]
        if self.labelRoiZdepthSpinbox.value() == 0:
            self.labelRoiZdepthSpinbox.setValue(posData.SizeZ)
        self.labelRoiZdepthSpinbox.setMaximum(posData.SizeZ+1)
    
    def gui_createOverlayColors(self):
        fluoChannels = [ch for ch in self.ch_names if ch != self.user_ch_name]
        self.overlayColors = {}
        for c, ch in enumerate(fluoChannels):
            if f'{ch}_rgb' in self.df_settings.index:
                rgb_text = self.df_settings.at[f'{ch}_rgb', 'value']
                rgb = tuple([int(val) for val in rgb_text.split('_')])
                self.overlayColors[ch] = rgb
            else:
                self.overlayColors[ch] = self.overlayRGBs[c]
    
    def gui_createOverlayItems(self):
        self.imgGrad.setAxisLabel(self.user_ch_name)
        self.overlayLayersItems = {}
        fluoChannels = [ch for ch in self.ch_names if ch != self.user_ch_name]
        for c, ch in enumerate(fluoChannels):
            overlayItems = self.getOverlayItems(ch)                
            self.overlayLayersItems[ch] = overlayItems
            imageItem, lutItem, alphaScrollBar = overlayItems
            self.ax1.addItem(imageItem)
            self.lutItemsLayout.addItem(lutItem, row=0, col=c+1)
        self.plotsCol = len(self.ch_names)

    def gui_getLostObjScatterItem(self):
        self.objLostAnnotRgb = (245, 184, 0)
        brush = pg.mkBrush((*self.objLostAnnotRgb, 150))
        pen = pg.mkPen(self.objLostAnnotRgb, width=1)
        lostObjScatterItem = pg.ScatterPlotItem(
            size=self.contLineWeight+1, pen=pen, 
            brush=brush, pxMode=False, symbol='s'
        )
        return lostObjScatterItem

    def _gui_createGraphicsItems(self):
        posData = self.data[self.pos_i]
        allIDs = set()
        if np.any(self.data[self.pos_i].segm_data):
            self.logger.info('Counting total number of segmented objects...')
            for lab in tqdm(self.data[self.pos_i].segm_data, ncols=100):
                IDs = [obj.label for obj in skimage.measure.regionprops(lab)]
                allIDs.update(IDs)
        if not allIDs:
            allIDs = list(range(100))
        
        self.highLowResToggle.setChecked(True)
        numItems = len(allIDs)
        if numItems > 1500:
            cancel, switchToLowRes = _warnings.warnTooManyItems(
                self, numItems, self.progressWin
            )
            if cancel:
                self.progressWin.workerFinished = True
                self.progressWin.close()
                self.loadingDataAborted()
                return
            if switchToLowRes:
                self.highLowResToggle.setChecked(False)
            else:
                # Many items requires pxMode active to be fast enough
                self.pxModeToggle.setChecked(True)

        self.logger.info(f'Creating graphical items...')

        self.ax1_contoursImageItem = pg.ImageItem()
        self.ax1_oldMothBudLinesItem = pg.ScatterPlotItem(
            symbol='s', pxMode=False, brush=self.oldMothBudLineBrush,
            size=self.mothBudLineWeight, pen=None
        )
        self.ax1_newMothBudLinesItem = pg.ScatterPlotItem(
            symbol='s', pxMode=False, brush=self.newMothBudLineBrush,
            size=self.mothBudLineWeight, pen=None
        )
        self.ax1_lostObjScatterItem = self.gui_getLostObjScatterItem()

        brush = pg.mkBrush((0,255,0,200))
        pen = pg.mkPen('g', width=1)
        self.ccaFailedScatterItem = pg.ScatterPlotItem(
            size=self.contLineWeight+1, pen=pen, 
            brush=brush, pxMode=False, symbol='s'
        )

        self.ax2_contoursImageItem = pg.ImageItem()
        self.ax2_oldMothBudLinesItem = pg.ScatterPlotItem(
            symbol='s', pxMode=False, brush=self.oldMothBudLineBrush,
            size=self.mothBudLineWeight, pen=None
        )
        self.ax2_newMothBudLinesItem = pg.ScatterPlotItem(
            symbol='s', pxMode=False, brush=self.newMothBudLineBrush,
            size=self.mothBudLineWeight, pen=None
        )
        self.ax2_lostObjScatterItem = self.gui_getLostObjScatterItem()

        self.gui_createTextAnnotItems(allIDs)
        self.gui_setTextAnnotColors()

        self.setDisabledAnnotOptions(False)
        
        self.progressWin.mainPbar.setMaximum(0)
        self.gui_addOverlayLayerItems()
        self.gui_addTopLayerItems()

        self.gui_addCreatedAxesItems()
        self.gui_add_ax_cursors()
        self.progressWin.workerFinished = True
        self.progressWin.close()

        self.loadingDataCompleted()
    
    def gui_createTextAnnotItems(self, allIDs):
        self.textAnnot = {}
        isHighResolution = self.highLowResToggle.isChecked()
        pxMode = self.pxModeToggle.isChecked()
        for ax in range(2):
            ax_textAnnot = annotate.TextAnnotations()
            ax_textAnnot.initFonts(self.fontSize)
            ax_textAnnot.createItems(
                isHighResolution, allIDs, pxMode=pxMode
            )
            self.textAnnot[ax] = ax_textAnnot
    
    def gui_addOverlayLayerItems(self):
        for items in self.overlayLabelsItems.values():
            imageItem, contoursItem, gradItem = items
            self.ax1.addItem(imageItem)
            self.ax1.addItem(contoursItem)
    
    def gui_addTopLayerItems(self):
        for item in self.topLayerItems:
            self.ax1.addItem(item)
        
        for item in self.topLayerItemsRight:
            self.ax2.addItem(item)
    
    def gui_createMothBudLinePens(self):
        if 'mothBudLineSize' in self.df_settings.index:
            val = self.df_settings.at['mothBudLineSize', 'value']
            self.mothBudLineWeight = int(val)
        else:
            self.mothBudLineWeight = 2

        self.newMothBudlineColor = (255, 0, 0)            
        if 'mothBudLineColor' in self.df_settings.index:
            val = self.df_settings.at['mothBudLineColor', 'value']
            rgba = colors.rgba_str_to_values(val)
            self.mothBudLineColor = rgba[0:3]
        else:
            self.mothBudLineColor = (255,165,0)

        try:
            self.imgGrad.mothBudLineColorButton.sigColorChanging.disconnect()
            self.imgGrad.mothBudLineColorButton.sigColorChanged.disconnect()
        except Exception as e:
            pass
        try:
            for act in self.imgGrad.mothBudLineWightActionGroup.actions():
                act.toggled.disconnect()
        except Exception as e:
            pass
        for act in self.imgGrad.mothBudLineWightActionGroup.actions():
            if act.lineWeight == self.mothBudLineWeight:
                act.setChecked(True)
            else:
                act.setChecked(False)
        self.imgGrad.mothBudLineColorButton.setColor(self.mothBudLineColor[:3])

        self.imgGrad.mothBudLineColorButton.sigColorChanging.connect(
            self.updateMothBudLineColour
        )
        self.imgGrad.mothBudLineColorButton.sigColorChanged.connect(
            self.saveMothBudLineColour
        )
        for act in self.imgGrad.mothBudLineWightActionGroup.actions():
            act.toggled.connect(self.mothBudLineWeightToggled)

        # MOther-bud lines brushes
        self.NewBudMoth_Pen = pg.mkPen(
            color=self.newMothBudlineColor, width=self.mothBudLineWeight+1, 
            style=Qt.DashLine
        )
        self.OldBudMoth_Pen = pg.mkPen(
            color=self.mothBudLineColor, width=self.mothBudLineWeight, 
            style=Qt.DashLine
        )

        self.oldMothBudLineBrush = pg.mkBrush(self.mothBudLineColor)
        self.newMothBudLineBrush = pg.mkBrush(self.newMothBudlineColor)

    def gui_createContourPens(self):
        if 'contLineWeight' in self.df_settings.index:
            val = self.df_settings.at['contLineWeight', 'value']
            self.contLineWeight = int(val)
        else:
            self.contLineWeight = 1
        if 'contLineColor' in self.df_settings.index:
            val = self.df_settings.at['contLineColor', 'value']
            rgba = colors.rgba_str_to_values(val)
            self.contLineColor = rgba
            self.newIDlineColor = [min(255, v+50) for v in self.contLineColor]
        else:
            self.contLineColor = (255, 0, 0, 200)
            self.newIDlineColor = (255, 0, 0, 255)

        try:
            self.imgGrad.contoursColorButton.sigColorChanging.disconnect()
            self.imgGrad.contoursColorButton.sigColorChanged.disconnect()
        except Exception as e:
            pass
        try:
            for act in self.imgGrad.contLineWightActionGroup.actions():
                act.toggled.disconnect()
        except Exception as e:
            pass
        for act in self.imgGrad.contLineWightActionGroup.actions():
            if act.lineWeight == self.contLineWeight:
                act.setChecked(True)
        self.imgGrad.contoursColorButton.setColor(self.contLineColor[:3])

        self.imgGrad.contoursColorButton.sigColorChanging.connect(
            self.updateContColour
        )
        self.imgGrad.contoursColorButton.sigColorChanged.connect(
            self.saveContColour
        )
        for act in self.imgGrad.contLineWightActionGroup.actions():
            act.toggled.connect(self.contLineWeightToggled)

        # Contours pens
        self.oldIDs_cpen = pg.mkPen(
            color=self.contLineColor, width=self.contLineWeight
        )
        self.newIDs_cpen = pg.mkPen(
            color=self.newIDlineColor, width=self.contLineWeight+1
        )
        self.tempNewIDs_cpen = pg.mkPen(
            color='g', width=self.contLineWeight+1
        )

    def gui_createGraphicsItems(self):
        # Create enough PlotDataItems and LabelItems to draw contours and IDs.
        self.progressWin = apps.QDialogWorkerProgress(
            title='Creating axes items', parent=self,
            pbarDesc='Creating axes items (see progress in the terminal)...'
        )
        self.progressWin.show(self.app)
        self.progressWin.mainPbar.setMaximum(0)

        QTimer.singleShot(50, self._gui_createGraphicsItems)

    def gui_connectGraphicsEvents(self):
        self.img1.hoverEvent = self.gui_hoverEventImg1
        self.img2.hoverEvent = self.gui_hoverEventImg2
        self.img1.mousePressEvent = self.gui_mousePressEventImg1
        self.img1.mouseMoveEvent = self.gui_mouseDragEventImg1
        self.img1.mouseReleaseEvent = self.gui_mouseReleaseEventImg1
        self.img2.mousePressEvent = self.gui_mousePressEventImg2
        self.img2.mouseMoveEvent = self.gui_mouseDragEventImg2
        self.img2.mouseReleaseEvent = self.gui_mouseReleaseEventImg2
        self.rightImageItem.mousePressEvent = self.gui_mousePressRightImage
        self.rightImageItem.mouseMoveEvent = self.gui_mouseDragRightImage
        self.rightImageItem.mouseReleaseEvent = self.gui_mouseReleaseRightImage
        self.rightImageItem.hoverEvent = self.gui_hoverEventRightImage
        # self.imgGrad.gradient.showMenu = self.gui_gradientContextMenuEvent
        self.imgGradRight.gradient.showMenu = self.gui_rightImageShowContextMenu
        # self.imgGrad.vb.contextMenuEvent = self.gui_gradientContextMenuEvent

    def gui_initImg1BottomWidgets(self):
        self.zSliceScrollBar.hide()
        self.zProjComboBox.hide()
        self.zSliceOverlay_SB.hide()
        self.zProjOverlay_CB.hide()
        self.overlay_z_label.hide()
        self.zSliceCheckbox.hide()
        self.zSliceSpinbox.hide()
        self.SizeZlabel.hide()

    @exception_handler
    def gui_mousePressEventImg2(self, event):
        modifiers = QGuiApplication.keyboardModifiers()
        ctrl = modifiers == Qt.ControlModifier
        alt = modifiers == Qt.AltModifier
        isMod = alt
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        left_click = event.button() == Qt.MouseButton.LeftButton and not isMod
        middle_click = self.isMiddleClick(event, modifiers)
        right_click = event.button() == Qt.MouseButton.RightButton and not isMod
        isPanImageClick = self.isPanImageClick(event, modifiers)
        eraserON = self.eraserButton.isChecked()
        brushON = self.brushButton.isChecked()
        separateON = self.separateBudButton.isChecked()
        self.typingEditID = False

        # Drag image if neither brush or eraser are On pressed
        dragImg = (
            left_click and not eraserON and not
            brushON and not separateON and not middle_click
        )
        if isPanImageClick:
            dragImg = True

        # Enable dragging of the image window like pyqtgraph original code
        if dragImg:
            pg.ImageItem.mousePressEvent(self.img2, event)
            event.ignore()
            return

        if mode == 'Viewer' and middle_click:
            self.startBlinkingModeCB()
            event.ignore()
            return

        x, y = event.pos().x(), event.pos().y()
        xdata, ydata = int(x), int(y)
        Y, X = self.get_2Dlab(posData.lab).shape
        if xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y:
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
        else:
            return

        # Check if right click on ROI
        isClickOnDelRoi = self.gui_clickedDelRoi(event, left_click, right_click)
        if isClickOnDelRoi:
            return

        # show gradient widget menu if none of the right-click actions are ON
        # and event is not coming from image 1
        is_right_click_action_ON = any([
            b.isChecked() for b in self.checkableQButtonsGroup.buttons()
        ])
        is_right_click_custom_ON = any([
            b.isChecked() for b in self.customAnnotDict.keys()
        ])
        is_event_from_img1 = False
        if hasattr(event, 'isImg1Sender'):
            is_event_from_img1 = event.isImg1Sender
        showLabelsGradMenu = (
            right_click and not is_right_click_action_ON
            and not is_event_from_img1 and not middle_click
        )
        if showLabelsGradMenu:
            self.labelsGrad.showMenu(event)
            event.ignore()
            return

        editInViewerMode = (
            (is_right_click_action_ON or is_right_click_custom_ON)
            and (right_click or middle_click) and mode=='Viewer'
        )

        if editInViewerMode:
            self.startBlinkingModeCB()
            event.ignore()
            return

        # Left-click is used for brush, eraser, separate bud, curvature tool
        # and magic labeller
        # Brush and eraser are mutually exclusive but we want to keep the eraser
        # or brush ON and disable them temporarily to allow left-click with
        # separate ON
        canErase = eraserON and not separateON and not dragImg
        canBrush = brushON and not separateON and not dragImg
        canDelete = mode == 'Segmentation and Tracking' or self.isSnapshot

        # Erase with brush and left click on the right image
        # NOTE: contours, IDs and rp will be updated
        # on gui_mouseReleaseEventImg2
        if left_click and canErase:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            Y, X = self.get_2Dlab(posData.lab).shape
            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)
            self.yPressAx2, self.xPressAx2 = y, x
            # Keep a global mask to compute which IDs got erased
            self.erasedIDs = []
            lab_2D = self.get_2Dlab(posData.lab)
            self.erasedID = self.getHoverID(xdata, ydata)

            ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)

            # Build eraser mask
            mask = np.zeros(lab_2D.shape, bool)
            mask[ymin:ymax, xmin:xmax][diskMask] = True

            # If user double-pressed 'b' then erase over ALL labels
            color = self.eraserButton.palette().button().color().name()
            eraseOnlyOneID = (
                color != self.doublePressKeyButtonColor
                and self.erasedID != 0
            )
            if eraseOnlyOneID:
                mask[lab_2D!=self.erasedID] = False

            self.eraseOnlyOneID = eraseOnlyOneID

            self.erasedIDs.extend(lab_2D[mask])
            self.setTempImg1Eraser(mask, init=True)
            self.applyEraserMask(mask)
            self.setImageImg2()

            self.isMouseDragImg2 = True

        # Paint with brush and left click on the right image
        # NOTE: contours, IDs and rp will be updated
        # on gui_mouseReleaseEventImg2
        elif left_click and canBrush:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            lab_2D = self.get_2Dlab(posData.lab)
            Y, X = lab_2D.shape
            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)
            self.yPressAx2, self.xPressAx2 = y, x

            ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)
            diskSlice = (slice(ymin, ymax), slice(xmin, xmax))

            ID = self.getHoverID(xdata, ydata)

            if ID > 0:
                self.ax2BrushID = ID
                self.isNewID = False
            else:
                self.setBrushID()
                self.ax2BrushID = posData.brushID
                self.isNewID = True

            self.updateLookuptable(lenNewLut=self.ax2BrushID+1)
            self.isMouseDragImg2 = True

            # Draw new objects
            localLab = lab_2D[diskSlice]
            mask = diskMask.copy()
            if not self.isPowerBrush():
                mask[localLab!=0] = False

            self.applyBrushMask(mask, self.ax2BrushID, toLocalSlice=diskSlice)

            self.setImageImg2(updateLookuptable=False)
            self.lastHoverID = -1

        # Delete entire ID (set to 0)
        elif middle_click and canDelete:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            delID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if delID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                delID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                        'Enter here ID that you want to delete',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                delID_prompt.exec_()
                if delID_prompt.cancel:
                    return
                delID = delID_prompt.EntryID

            # Ask to propagate change to all future visited frames
            (UndoFutFrames, applyFutFrames, endFrame_i,
            doNotShowAgain) = self.propagateChange(
                delID, 'Delete ID', posData.doNotShowAgain_DelID,
                posData.UndoFutFrames_DelID, posData.applyFutFrames_DelID
            )

            if UndoFutFrames is None:
                return

            posData.doNotShowAgain_DelID = doNotShowAgain
            posData.UndoFutFrames_DelID = UndoFutFrames
            posData.applyFutFrames_DelID = applyFutFrames
            includeUnvisited = posData.includeUnvisitedInfo['Delete ID']

            self.current_frame_i = posData.frame_i

            # Apply Delete ID to future frames if requested
            if applyFutFrames:
                # Store current data before going to future frames
                self.store_data()
                segmSizeT = len(posData.segm_data)
                for i in range(posData.frame_i+1, segmSizeT):
                    lab = posData.allData_li[i]['labels']
                    if lab is None and not includeUnvisited:
                        self.enqAutosave()
                        break
                    
                    if lab is not None:
                        # Visited frame
                        lab[lab==delID] = 0

                        # Store change
                        posData.allData_li[i]['labels'] = lab.copy()
                        # Get the rest of the stored metadata based on the new lab
                        posData.frame_i = i
                        self.get_data()
                        self.store_data(autosave=False)
                    elif includeUnvisited:
                        # Unvisited frame (includeUnvisited = True)
                        lab = posData.segm_data[i]
                        lab[lab==delID] = 0

            # Back to current frame
            if applyFutFrames:
                posData.frame_i = self.current_frame_i
                self.get_data()

            # Store undo state before modifying stuff
            self.storeUndoRedoStates(UndoFutFrames)     

            self.clearObjContour(ID=delID, ax=0)     
            self.clearObjContour(ID=delID, ax=1)       

            delID_mask = posData.lab==delID
            posData.lab[delID_mask] = 0

            # Update data (rp, etc)
            self.update_rp()

            self.setAllTextAnnotations()

            if self.isSnapshot:
                self.fixCcaDfAfterEdit('Delete ID')
            else:
                self.warnEditingWithCca_df('Delete ID')

            self.setImageImg2()

            how = self.drawIDsContComboBox.currentText()
            if how.find('overlay segm. masks') != -1:
                if delID_mask.ndim == 3:
                    delID_mask = delID_mask[self.z_lab()]
                self.labelsLayerImg1.image[delID_mask] = 0
                self.labelsLayerImg1.setImage(self.labelsLayerImg1.image)
            
            how_ax2 = self.getAnnotateHowRightImage()
            if how_ax2.find('overlay segm. masks') != -1:
                if delID_mask.ndim == 3:
                    delID_mask = delID_mask[self.z_lab()]
                self.labelsLayerRightImg.image[delID_mask] = 0
                self.labelsLayerRightImg.setImage(self.labelsLayerRightImg.image)

            self.highlightLostNew()

        # Separate bud
        elif right_click and separateON:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(self.get_2Dlab(posData.lab), y, x)
                sepID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter here ID that you want to split',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                sepID_prompt.exec_()
                if sepID_prompt.cancel:
                    return
                else:
                    ID = sepID_prompt.EntryID

            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)
            max_ID = max(posData.IDs, default=1)

            if not ctrl:
                lab2D, success = self.auto_separate_bud_ID(
                    ID, self.get_2Dlab(posData.lab), posData.rp, max_ID,
                    enforce=True
                )
                self.set_2Dlab(lab2D)
            else:
                success = False

            # If automatic bud separation was not successfull call manual one
            if not success:
                posData.disableAutoActivateViewerWindow = True
                img = self.getDisplayedImg1()
                col = 'manual_separate_draw_mode'
                drawMode = self.df_settings.at[col, 'value']
                manualSep = apps.manualSeparateGui(
                    self.get_2Dlab(posData.lab), ID, img,
                    fontSize=self.fontSize,
                    IDcolor=self.lut[ID],
                    parent=self,
                    drawMode=drawMode
                )
                manualSep.show()
                manualSep.centerWindow()
                loop = QEventLoop(self)
                manualSep.loop = loop
                loop.exec_()
                if manualSep.cancel:
                    posData.disableAutoActivateViewerWindow = False
                    if not self.separateBudButton.findChild(QAction).isChecked():
                        self.separateBudButton.setChecked(False)
                    return
                lab2D = self.get_2Dlab(posData.lab)
                lab2D[manualSep.lab!=0] = manualSep.lab[manualSep.lab!=0]
                self.set_2Dlab(lab2D)
                posData.disableAutoActivateViewerWindow = False
                self.storeManualSeparateDrawMode(manualSep.drawMode)

            # Update data (rp, etc)
            prev_IDs = [obj.label for obj in posData.rp]
            self.update_rp()

            # Repeat tracking
            self.tracking(enforce=True, assign_unique_new_IDs=False)

            if self.isSnapshot:
                self.fixCcaDfAfterEdit('Separate IDs')
                self.updateAllImages()
            else:
                self.warnEditingWithCca_df('Separate IDs')

            self.store_data()

            if not self.separateBudButton.findChild(QAction).isChecked():
                self.separateBudButton.setChecked(False)

        # Fill holes
        elif right_click and self.fillHolesToolButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                clickedBkgrID = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter here the ID that you want to '
                         'fill the holes of',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                clickedBkgrID.exec_()
                if clickedBkgrID.cancel:
                    return
                else:
                    ID = clickedBkgrID.EntryID

            if ID in posData.lab:
                # Store undo state before modifying stuff
                self.storeUndoRedoStates(False)
                obj_idx = posData.IDs.index(ID)
                obj = posData.rp[obj_idx]
                objMask = self.getObjImage(obj.image, obj.bbox)
                localFill = scipy.ndimage.binary_fill_holes(objMask)
                posData.lab[self.getObjSlice(obj.slice)][localFill] = ID

                self.update_rp()
                self.updateAllImages()

                if not self.fillHolesToolButton.findChild(QAction).isChecked():
                    self.fillHolesToolButton.setChecked(False)

        # Hull contour
        elif right_click and self.hullContToolButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                mergeID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter here the ID that you want to '
                         'replace with Hull contour',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                mergeID_prompt.exec_()
                if mergeID_prompt.cancel:
                    return
                else:
                    ID = mergeID_prompt.EntryID

            if ID in posData.lab:
                # Store undo state before modifying stuff
                self.storeUndoRedoStates(False)
                obj_idx = posData.IDs.index(ID)
                obj = posData.rp[obj_idx]
                objMask = self.getObjImage(obj.image, obj.bbox)
                localHull = skimage.morphology.convex_hull_image(objMask)
                posData.lab[self.getObjSlice(obj.slice)][localHull] = ID

                self.update_rp()
                self.updateAllImages()

                if not self.hullContToolButton.findChild(QAction).isChecked():
                    self.hullContToolButton.setChecked(False)

        # Move label
        elif right_click and self.moveLabelToolButton.isChecked():
            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)

            x, y = event.pos().x(), event.pos().y()
            self.startMovingLabel(x, y)

        # Fill holes
        elif right_click and self.fillHolesToolButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                clickedBkgrID = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter here the ID that you want to '
                         'fill the holes of',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                clickedBkgrID.exec_()
                if clickedBkgrID.cancel:
                    return
                else:
                    ID = clickedBkgrID.EntryID

        # Merge IDs
        elif right_click and self.mergeIDsButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                mergeID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter here first ID that you want to merge',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                mergeID_prompt.exec_()
                if mergeID_prompt.cancel:
                    return
                else:
                    ID = mergeID_prompt.EntryID

            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)
            self.firstID = ID

        # Edit ID
        elif right_click and self.editIDbutton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                editID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter here ID that you want to replace with a new one',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                editID_prompt.show(block=True)

                if editID_prompt.cancel:
                    return
                else:
                    ID = editID_prompt.EntryID

            obj_idx = posData.IDs.index(ID)
            y, x = posData.rp[obj_idx].centroid[-2:]
            xdata, ydata = int(x), int(y)

            posData.disableAutoActivateViewerWindow = True
            prev_IDs = posData.IDs.copy()
            editID = apps.editID_QWidget(
                ID, posData.IDs, doNotShowAgain=self.doNotAskAgainExistingID,
                parent=self
            )
            editID.show(block=True)
            if editID.cancel:
                posData.disableAutoActivateViewerWindow = False
                if not self.editIDbutton.findChild(QAction).isChecked():
                    self.editIDbutton.setChecked(False)
                return

            if not self.doNotAskAgainExistingID:    
                self.editIDmergeIDs = editID.mergeWithExistingID
            self.doNotAskAgainExistingID = editID.doNotAskAgainExistingID
            

            # Ask to propagate change to all future visited frames
            (UndoFutFrames, applyFutFrames, endFrame_i,
            doNotShowAgain) = self.propagateChange(
                ID, 'Edit ID', posData.doNotShowAgain_EditID,
                posData.UndoFutFrames_EditID, posData.applyFutFrames_EditID,
                applyTrackingB=True
            )

            if UndoFutFrames is None:
                return

            # Store undo state before modifying stuff
            self.storeUndoRedoStates(UndoFutFrames)
            maxID = max(posData.IDs, default=0)
            for old_ID, new_ID in editID.how:
                if new_ID in prev_IDs and not self.editIDmergeIDs:
                    tempID = maxID + 1
                    posData.lab[posData.lab == old_ID] = maxID + 1
                    posData.lab[posData.lab == new_ID] = old_ID
                    posData.lab[posData.lab == tempID] = new_ID
                    maxID += 1

                    old_ID_idx = prev_IDs.index(old_ID)
                    new_ID_idx = prev_IDs.index(new_ID)

                    # Append information for replicating the edit in tracking
                    # List of tuples (y, x, replacing ID)
                    objo = posData.rp[old_ID_idx]
                    yo, xo = self.getObjCentroid(objo.centroid)
                    objn = posData.rp[new_ID_idx]
                    yn, xn = self.getObjCentroid(objn.centroid)
                    if not math.isnan(yo) and not math.isnan(yn):
                        yn, xn = int(yn), int(xn)
                        posData.editID_info.append((yn, xn, new_ID))
                        yo, xo = int(y), int(x)
                        posData.editID_info.append((yo, xo, old_ID))
                else:
                    posData.lab[posData.lab == old_ID] = new_ID
                    if new_ID > maxID:
                        maxID = new_ID
                    old_ID_idx = posData.IDs.index(old_ID)

                    # Append information for replicating the edit in tracking
                    # List of tuples (y, x, replacing ID)
                    obj = posData.rp[old_ID_idx]
                    y, x = self.getObjCentroid(obj.centroid)
                    if not math.isnan(y) and not math.isnan(y):
                        y, x = int(y), int(x)
                        posData.editID_info.append((y, x, new_ID))

            # Update rps
            self.update_rp()

            # Since we manually changed an ID we don't want to repeat tracking
            self.setAllTextAnnotations()
            self.highlightLostNew()
            # self.checkIDsMultiContour()

            # Update colors for the edited IDs
            self.updateLookuptable()

            if self.isSnapshot:
                self.fixCcaDfAfterEdit('Edit ID')
                self.updateAllImages()
            else:
                self.warnEditingWithCca_df('Edit ID')

            if not self.editIDbutton.findChild(QAction).isChecked():
                self.editIDbutton.setChecked(False)

            posData.disableAutoActivateViewerWindow = True

            # Perform desired action on future frames
            posData.doNotShowAgain_EditID = doNotShowAgain
            posData.UndoFutFrames_EditID = UndoFutFrames
            posData.applyFutFrames_EditID = applyFutFrames
            includeUnvisited = posData.includeUnvisitedInfo['Edit ID']

            self.current_frame_i = posData.frame_i

            if applyFutFrames:
                # Store data for current frame
                self.store_data()
                if endFrame_i is None:
                    self.app.restoreOverrideCursor()
                    return
                segmSizeT = len(posData.segm_data)
                for i in range(posData.frame_i+1, segmSizeT):
                    lab = posData.allData_li[i]['labels']
                    if lab is None and not includeUnvisited:
                        self.enqAutosave()
                        break

                    if lab is not None:
                        # Visited frame
                        posData.frame_i = i
                        self.get_data()
                        if self.onlyTracking:
                            self.tracking(enforce=True)
                        else:
                            maxID = max(posData.IDs) + 1
                            for old_ID, new_ID in editID.how:
                                if new_ID in posData.lab:
                                    tempID = maxID + 1 # posData.lab.max() + 1
                                    posData.lab[posData.lab == old_ID] = tempID
                                    posData.lab[posData.lab == new_ID] = old_ID
                                    posData.lab[posData.lab == tempID] = new_ID
                                    maxID += 1
                                else:
                                    posData.lab[posData.lab == old_ID] = new_ID
                            self.update_rp(draw=False)
                        self.store_data(autosave=i==endFrame_i)
                    elif includeUnvisited:
                        # Unvisited frame (includeUnvisited = True)
                        lab = posData.segm_data[i]
                        for old_ID, new_ID in editID.how:
                            if new_ID in lab:
                                tempID = lab.max() + 1
                                lab[lab == old_ID] = tempID
                                lab[lab == new_ID] = old_ID
                                lab[lab == tempID] = new_ID
                            else:
                                lab[lab == old_ID] = new_ID

                # Back to current frame
                posData.frame_i = self.current_frame_i
                self.get_data()
                self.app.restoreOverrideCursor()
        
        elif (right_click or left_click) and self.keepIDsButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                keepID_win = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                        'Enter ID that you want to keep',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                keepID_win.exec_()
                if keepID_win.cancel:
                    return
                else:
                    ID = keepID_win.EntryID
            
            if ID in self.keptObjectsIDs:
                self.keptObjectsIDs.remove(ID)
                self.clearHighlightedText()
            else:
                self.keptObjectsIDs.append(ID)
                self.highlightLabelID(ID)
            
            self.updateTempLayerKeepIDs()

        # Annotate cell as removed from the analysis
        elif right_click and self.binCellButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                binID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter ID that you want to remove from the analysis',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                binID_prompt.exec_()
                if binID_prompt.cancel:
                    return
                else:
                    ID = binID_prompt.EntryID

            # Ask to propagate change to all future visited frames
            (UndoFutFrames, applyFutFrames, endFrame_i,
            doNotShowAgain) = self.propagateChange(
                ID, 'Exclude cell from analysis',
                posData.doNotShowAgain_BinID,
                posData.UndoFutFrames_BinID,
                posData.applyFutFrames_BinID
            )

            if UndoFutFrames is None:
                # User cancelled the process
                return

            posData.doNotShowAgain_BinID = doNotShowAgain
            posData.UndoFutFrames_BinID = UndoFutFrames
            posData.applyFutFrames_BinID = applyFutFrames

            self.current_frame_i = posData.frame_i

            # Apply Exclude cell from analysis to future frames if requested
            if applyFutFrames:
                # Store current data before going to future frames
                self.store_data()
                for i in range(posData.frame_i+1, endFrame_i+1):
                    posData.frame_i = i
                    self.get_data()
                    if ID in posData.binnedIDs:
                        posData.binnedIDs.remove(ID)
                    else:
                        posData.binnedIDs.add(ID)
                    self.update_rp_metadata(draw=False)
                    self.store_data(autosave=i==endFrame_i)

                self.app.restoreOverrideCursor()

            # Back to current frame
            if applyFutFrames:
                posData.frame_i = self.current_frame_i
                self.get_data()

            # Store undo state before modifying stuff
            self.storeUndoRedoStates(UndoFutFrames)

            if ID in posData.binnedIDs:
                posData.binnedIDs.remove(ID)
            else:
                posData.binnedIDs.add(ID)

            self.annotate_rip_and_bin_IDs(updateLabel=True)

            # Gray out ore restore binned ID
            self.updateLookuptable()

            if not self.binCellButton.findChild(QAction).isChecked():
                self.binCellButton.setChecked(False)

        # Annotate cell as dead
        elif right_click and self.ripCellButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                ripID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter ID that you want to annotate as dead',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                ripID_prompt.exec_()
                if ripID_prompt.cancel:
                    return
                else:
                    ID = ripID_prompt.EntryID

            # Ask to propagate change to all future visited frames
            (UndoFutFrames, applyFutFrames, endFrame_i,
            doNotShowAgain) = self.propagateChange(
                ID, 'Annotate cell as dead',
                posData.doNotShowAgain_RipID,
                posData.UndoFutFrames_RipID,
                posData.applyFutFrames_RipID
            )

            if UndoFutFrames is None:
                return

            posData.doNotShowAgain_RipID = doNotShowAgain
            posData.UndoFutFrames_RipID = UndoFutFrames
            posData.applyFutFrames_RipID = applyFutFrames

            self.current_frame_i = posData.frame_i

            # Apply Edit ID to future frames if requested
            if applyFutFrames:
                # Store current data before going to future frames
                self.store_data()
                for i in range(posData.frame_i+1, endFrame_i+1):
                    posData.frame_i = i
                    self.get_data()
                    if ID in posData.ripIDs:
                        posData.ripIDs.remove(ID)
                    else:
                        posData.ripIDs.add(ID)
                    self.update_rp_metadata(draw=False)
                    self.store_data(autosave=i==endFrame_i)
                self.app.restoreOverrideCursor()

            # Back to current frame
            if applyFutFrames:
                posData.frame_i = self.current_frame_i
                self.get_data()

            # Store undo state before modifying stuff
            self.storeUndoRedoStates(UndoFutFrames)

            if ID in posData.ripIDs:
                posData.ripIDs.remove(ID)
            else:
                posData.ripIDs.add(ID)

            self.annotate_rip_and_bin_IDs(updateLabel=True)

            # Gray out dead ID
            self.updateLookuptable()
            self.store_data()

            if self.isSnapshot:
                self.fixCcaDfAfterEdit('Annotate ID as dead')
                self.updateAllImages()
            else:
                self.warnEditingWithCca_df('Annotate ID as dead')

            if not self.ripCellButton.findChild(QAction).isChecked():
                self.ripCellButton.setChecked(False)

    def expandLabelCallback(self, checked):
        self.disconnectLeftClickButtons()
        self.uncheckLeftClickButtons(self.sender())
        self.connectLeftClickButtons()
        self.expandFootprintSize = 1

    def expandLabel(self, dilation=True):
        posData = self.data[self.pos_i]
        if self.hoverLabelID == 0:
            self.isExpandingLabel = False
            return

        # Re-initialize label to expand when we hover on a different ID
        # or we change direction
        reinitExpandingLab = (
            self.expandingID != self.hoverLabelID
            or dilation != self.isDilation
        )

        ID = self.hoverLabelID

        obj = posData.rp[posData.IDs.index(ID)]

        if reinitExpandingLab:
            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)
            # hoverLabelID different from previously expanded ID --> reinit
            self.isExpandingLabel = True
            self.expandingID = ID
            self.expandingLab = np.zeros_like(self.currentLab2D)
            self.expandingLab[obj.coords[:,-2], obj.coords[:,-1]] = ID
            self.expandFootprintSize = 1

        prevCoords = (obj.coords[:,-2], obj.coords[:,-1])
        self.currentLab2D[obj.coords[:,-2], obj.coords[:,-1]] = 0
        lab_2D = self.get_2Dlab(posData.lab)
        lab_2D[obj.coords[:,-2], obj.coords[:,-1]] = 0

        footprint = skimage.morphology.disk(self.expandFootprintSize)
        if dilation:
            expandedLab = skimage.morphology.dilation(
                self.expandingLab, footprint
            )
            self.isDilation = True
        else:
            expandedLab = skimage.morphology.erosion(
                self.expandingLab, footprint
            )
            self.isDilation = False

        # Prevent expanding into neighbouring labels
        expandedLab[self.currentLab2D>0] = 0

        # Get coords of the dilated/eroded object
        expandedObj = skimage.measure.regionprops(expandedLab)[0]
        expandedObjCoords = (expandedObj.coords[:,-2], expandedObj.coords[:,-1])

        # Add the dilated/erored object
        self.currentLab2D[expandedObjCoords] = self.expandingID
        lab_2D[expandedObjCoords] = self.expandingID

        self.set_2Dlab(lab_2D)

        self.update_rp()
        self.currentLab2D = lab_2D
        if self.labelsGrad.showLabelsImgAction.isChecked():
            self.img2.setImage(img=self.currentLab2D, autoLevels=False)

        self.setTempImg1ExpandLabel(prevCoords, expandedObjCoords)

    def startMovingLabel(self, xPos, yPos):
        posData = self.data[self.pos_i]
        xdata, ydata = int(xPos), int(yPos)
        lab_2D = self.get_2Dlab(posData.lab)
        ID = lab_2D[ydata, xdata]
        if ID == 0:
            self.isMovingLabel = False
            return

        posData = self.data[self.pos_i]
        self.isMovingLabel = True

        self.searchedIDitemRight.setData([], [])
        self.searchedIDitemLeft.setData([], [])
        self.movingID = ID
        self.prevMovePos = (xdata, ydata)
        movingObj = posData.rp[posData.IDs.index(ID)]
        self.movingObjCoords = movingObj.coords.copy()
        yy, xx = movingObj.coords[:,-2], movingObj.coords[:,-1]
        self.currentLab2D[yy, xx] = 0

    def dragLabel(self, xPos, yPos):
        posData = self.data[self.pos_i]
        lab_2D = self.get_2Dlab(posData.lab)
        Y, X = lab_2D.shape
        xdata, ydata = int(xPos), int(yPos)
        if xdata<0 or ydata<0 or xdata>=X or ydata>=Y:
            return

        self.clearObjContour(ID=self.movingID, ax=0)

        xStart, yStart = self.prevMovePos
        deltaX = xdata-xStart
        deltaY = ydata-yStart

        yy, xx = self.movingObjCoords[:,-2], self.movingObjCoords[:,-1]

        if self.isSegm3D:
            zz = self.movingObjCoords[:,0]
            posData.lab[zz, yy, xx] = 0
        else:
            posData.lab[yy, xx] = 0

        self.movingObjCoords[:,-2] = self.movingObjCoords[:,-2]+deltaY
        self.movingObjCoords[:,-1] = self.movingObjCoords[:,-1]+deltaX

        yy, xx = self.movingObjCoords[:,-2], self.movingObjCoords[:,-1]

        yy[yy<0] = 0
        xx[xx<0] = 0
        yy[yy>=Y] = Y-1
        xx[xx>=X] = X-1

        if self.isSegm3D:
            zz = self.movingObjCoords[:,0]
            posData.lab[zz, yy, xx] = self.movingID
        else:
            posData.lab[yy, xx] = self.movingID
        
        self.currentLab2D = self.get_2Dlab(posData.lab)
        if self.labelsGrad.showLabelsImgAction.isChecked():
            self.img2.setImage(self.currentLab2D, autoLevels=False)
        
        self.setTempImg1MoveLabel()

        self.prevMovePos = (xdata, ydata)

    @exception_handler
    def gui_mouseDragEventImg1(self, event):
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        if mode == 'Viewer':
            return

        Y, X = self.get_2Dlab(posData.lab).shape
        x, y = event.pos().x(), event.pos().y()
        xdata, ydata = int(x), int(y)
        if not myutils.is_in_bounds(xdata, ydata, X, Y):
            return

        if self.isRightClickDragImg1 and self.curvToolButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            self.drawAutoContour(y, x)

        # Brush dragging mouse --> keep painting
        elif self.isMouseDragImg1 and self.brushButton.isChecked():
            # t0 = time.perf_counter()

            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            lab_2D = self.get_2Dlab(posData.lab)
            Y, X = lab_2D.shape

            # t1 = time.perf_counter()

            ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)
            rrPoly, ccPoly = self.getPolygonBrush((y, x), Y, X)

            # t2 = time.perf_counter()

            diskSlice = (slice(ymin, ymax), slice(xmin, xmax))

            # Build brush mask
            mask = np.zeros(lab_2D.shape, bool)
            mask[diskSlice][diskMask] = True
            mask[rrPoly, ccPoly] = True

            # If user double-pressed 'b' then draw over the labels
            color = self.brushButton.palette().button().color().name()
            drawUnder = color != self.doublePressKeyButtonColor

            # t3 = time.perf_counter()
            if drawUnder:
                mask[lab_2D!=0] = False
                self.setHoverToolSymbolColor(
                    xdata, ydata, self.ax2_BrushCirclePen,
                    (self.ax2_BrushCircle, self.ax1_BrushCircle),
                    self.brushButton, brush=self.ax2_BrushCircleBrush
                )

            # t4 = time.perf_counter()

            # Apply brush mask
            self.applyBrushMask(mask, posData.brushID)

            self.setImageImg2(updateLookuptable=False)

            # t5 = time.perf_counter()

            lab2D = self.get_2Dlab(posData.lab)
            brushMask = np.logical_and(
                lab2D[diskSlice] == posData.brushID, diskMask
            )
            self.setTempImg1Brush(
                False, brushMask, posData.brushID, 
                toLocalSlice=diskSlice
            )

            # t6 = time.perf_counter()

            # printl(
            #     'Brush exec times =\n'
            #     f'  * {(t1-t0)*1000 = :.4f} ms\n'
            #     f'  * {(t2-t1)*1000 = :.4f} ms\n'
            #     f'  * {(t3-t2)*1000 = :.4f} ms\n'
            #     f'  * {(t4-t3)*1000 = :.4f} ms\n'
            #     f'  * {(t5-t4)*1000 = :.4f} ms\n'
            #     f'  * {(t6-t5)*1000 = :.4f} ms\n'
            #     f'  * {(t6-t0)*1000 = :.4f} ms'
            # )

        # Eraser dragging mouse --> keep erasing
        elif self.isMouseDragImg1 and self.eraserButton.isChecked():
            posData = self.data[self.pos_i]
            lab_2D = self.get_2Dlab(posData.lab)
            Y, X = lab_2D.shape
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            brushSize = self.brushSizeSpinbox.value()

            rrPoly, ccPoly = self.getPolygonBrush((y, x), Y, X)

            ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)

            diskSlice = (slice(ymin, ymax), slice(xmin, xmax))

            # Build eraser mask
            mask = np.zeros(lab_2D.shape, bool)
            mask[ymin:ymax, xmin:xmax][diskMask] = True
            mask[rrPoly, ccPoly] = True

            if self.eraseOnlyOneID:
                mask[lab_2D!=self.erasedID] = False
                self.setHoverToolSymbolColor(
                    xdata, ydata, self.eraserCirclePen,
                    (self.ax2_EraserCircle, self.ax1_EraserCircle),
                    self.eraserButton, hoverRGB=self.img2.lut[self.erasedID],
                    ID=self.erasedID
                )

            self.erasedIDs.extend(lab_2D[mask])
            self.applyEraserMask(mask)

            self.setImageImg2()

            for erasedID in np.unique(self.erasedIDs):
                if erasedID == 0:
                    continue
                self.erasedLab[lab_2D==erasedID] = erasedID
                self.erasedLab[mask] = 0

            eraserMask = mask[diskSlice]
            self.setTempImg1Eraser(eraserMask, toLocalSlice=diskSlice)
            self.setTempImg1Eraser(eraserMask, toLocalSlice=diskSlice, ax=1)

        # Move label dragging mouse --> keep moving
        elif self.isMovingLabel and self.moveLabelToolButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            self.dragLabel(x, y)

        # Wand dragging mouse --> keep doing the magic
        elif self.isMouseDragImg1 and self.wandToolButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            tol = self.wandToleranceSlider.value()
            flood_mask = skimage.segmentation.flood(
                self.flood_img, (ydata, xdata), tolerance=tol
            )
            drawUnderMask = np.logical_or(
                posData.lab==0, posData.lab==posData.brushID
            )
            flood_mask = np.logical_and(flood_mask, drawUnderMask)

            self.flood_mask[flood_mask] = True

            if self.wandAutoFillCheckbox.isChecked():
                self.flood_mask = scipy.ndimage.binary_fill_holes(
                    self.flood_mask
                )

            if np.any(self.flood_mask):
                mask = np.logical_or(
                    self.flood_mask,
                    posData.lab==posData.brushID
                )
                self.setTempImg1Brush(False, mask, posData.brushID)
        
        # Label ROI dragging mouse --> draw ROI
        elif self.isMouseDragImg1 and self.labelRoiButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            if self.labelRoiIsRectRadioButton.isChecked():
                x0, y0 = self.labelRoiItem.pos()
                w, h = (xdata-x0), (ydata-y0)
                self.labelRoiItem.setSize((w, h))
            elif self.labelRoiIsFreeHandRadioButton.isChecked():
                self.freeRoiItem.addPoint(xdata, ydata)
    
    # @exec_time
    def fillHolesID(self, ID, sender='brush'):
        posData = self.data[self.pos_i]
        if sender == 'brush':
            if not self.brushAutoFillCheckbox.isChecked():
                return
            
            try:
                obj_idx = posData.IDs.index(ID)
            except IndexError as e:
                return
            
            obj = posData.rp[obj_idx]
            objMask = self.getObjImage(obj.image, obj.bbox)
            localFill = scipy.ndimage.binary_fill_holes(objMask)
            objSlice = self.getObjSlice(obj.slice)
            lab2D = self.get_2Dlab(posData.lab)
            lab2D[objSlice][localFill] = ID
            self.set_2Dlab(lab2D)
            self.update_rp()

    def highlightIDcheckBoxToggled(self, checked):
        if not checked:
            self.highlightedID = 0
            self.initLookupTableLab()
        else:
            self.highlightedID = self.guiTabControl.propsQGBox.idSB.value()
            self.highlightSearchedID(self.highlightedID, force=True)
            self.updatePropsWidget(self.highlightedID)
        self.updateAllImages()

    def updatePropsWidget(self, ID):
        if isinstance(ID, str):
            # Function called by currentTextChanged of channelCombobox or
            # additionalMeasCombobox. We set self.currentPropsID = 0 to force update
            ID = self.guiTabControl.propsQGBox.idSB.value()
            self.currentPropsID = -1

        update = (
            self.propsDockWidget.isVisible()
            and ID != 0 and ID!=self.currentPropsID
        )
        if not update:
            return

        posData = self.data[self.pos_i]
        if posData.rp is None:
            self.update_rp()

        if not posData.IDs:
            # empty segmentation mask
            return

        if self.guiTabControl.highlightCheckbox.isChecked():
            self.highlightSearchedID(ID)

        propsQGBox = self.guiTabControl.propsQGBox

        if ID not in posData.IDs:
            s = f'Object ID {ID} does not exist'
            propsQGBox.notExistingIDLabel.setText(s)
            return

        propsQGBox.notExistingIDLabel.setText('')
        self.currentPropsID = ID
        propsQGBox.idSB.setValue(ID)
        obj_idx = posData.IDs.index(ID)
        obj = posData.rp[obj_idx]

        if self.isSegm3D:
            if self.zProjComboBox.currentText() == 'single z-slice':
                local_z = self.z_lab() - obj.bbox[0]
                area_pxl = np.count_nonzero(obj.image[local_z])
            else:
                area_pxl = np.count_nonzero(obj.image.max(axis=0))
        else:
            area_pxl = obj.area

        propsQGBox.cellAreaPxlSB.setValue(area_pxl)

        PhysicalSizeY = posData.PhysicalSizeY
        PhysicalSizeX = posData.PhysicalSizeX
        yx_pxl_to_um2 = PhysicalSizeY*PhysicalSizeX

        area_um2 = area_pxl*yx_pxl_to_um2

        propsQGBox.cellAreaUm2DSB.setValue(area_um2)

        if self.isSegm3D:
            PhysicalSizeZ = posData.PhysicalSizeZ
            vol_vox_3D = obj.area
            vol_fl_3D = vol_vox_3D*PhysicalSizeZ*PhysicalSizeY*PhysicalSizeX
            propsQGBox.cellVolVox3D_SB.setValue(vol_vox_3D)
            propsQGBox.cellVolFl3D_DSB.setValue(vol_fl_3D)

        vol_vox, vol_fl = _calc_rot_vol(
            obj, PhysicalSizeY, PhysicalSizeX
        )
        propsQGBox.cellVolVoxSB.setValue(int(vol_vox))
        propsQGBox.cellVolFlDSB.setValue(vol_fl)


        minor_axis_length = max(1, obj.minor_axis_length)
        elongation = obj.major_axis_length/minor_axis_length
        propsQGBox.elongationDSB.setValue(elongation)

        solidity = obj.solidity
        propsQGBox.solidityDSB.setValue(solidity)

        additionalPropName = propsQGBox.additionalPropsCombobox.currentText()
        additionalPropValue = getattr(obj, additionalPropName)
        propsQGBox.additionalPropsCombobox.indicator.setValue(additionalPropValue)

        intensMeasurQGBox = self.guiTabControl.intensMeasurQGBox
        selectedChannel = intensMeasurQGBox.channelCombobox.currentText()
        
        try:
            _, filename = self.getPathFromChName(selectedChannel, posData)
            image = posData.ol_data_dict[filename][posData.frame_i]
        except Exception as e:
            image = posData.img_data[posData.frame_i]

        if posData.SizeZ > 1:
            z = self.zSliceScrollBar.sliderPosition()
            objData = image[z][obj.slice][obj.image]
        else:
            objData = image[obj.slice][obj.image]

        intensMeasurQGBox.minimumDSB.setValue(np.min(objData))
        intensMeasurQGBox.maximumDSB.setValue(np.max(objData))
        intensMeasurQGBox.meanDSB.setValue(np.mean(objData))
        intensMeasurQGBox.medianDSB.setValue(np.median(objData))

        funcDesc = intensMeasurQGBox.additionalMeasCombobox.currentText()
        func = intensMeasurQGBox.additionalMeasCombobox.functions[funcDesc]
        if funcDesc == 'Concentration':
            bkgrVal = np.median(image[posData.lab == 0])
            amount = func(objData, bkgrVal, obj.area)
            value = amount/vol_vox
        elif funcDesc == 'Amount':
            bkgrVal = np.median(image[posData.lab == 0])
            amount = func(objData, bkgrVal, obj.area)
            value = amount
        else:
            value = func(objData)

        intensMeasurQGBox.additionalMeasCombobox.indicator.setValue(value)
    
    def gui_hoverEventRightImage(self, event):
        try:
            posData = self.data[self.pos_i]
        except AttributeError:
            return

        if event.isExit():
            self.ax1_cursor.setData([], [])

        self.gui_hoverEventImg1(event, isHoverImg1=False)
        setMirroredCursor = (
            self.app.overrideCursor() is None and not event.isExit()
        )
        if setMirroredCursor:
            x, y = event.pos()
            self.ax1_cursor.setData([x], [y])
        
    def gui_hoverEventImg1(self, event, isHoverImg1=True):
        try:
            posData = self.data[self.pos_i]
        except AttributeError:
            return
        
        # Update x, y, value label bottom right
        if not event.isExit():
            self.xHoverImg, self.yHoverImg = event.pos()
        else:
            self.xHoverImg, self.yHoverImg = None, None

        if event.isExit():
            self.ax2_cursor.setData([], [])

        # Cursor left image --> restore cursor
        if event.isExit():
            self.resetCursor()

        # Alt key was released --> restore cursor
        modifiers = QGuiApplication.keyboardModifiers()
        cursorsInfo = self.gui_setCursor(modifiers, event)
        
        drawRulerLine = (
            (self.rulerButton.isChecked() 
            or self.addDelPolyLineRoiAction.isChecked())
            and self.tempSegmentON and not event.isExit()
        )
        if drawRulerLine:
            x, y = event.pos()
            xdata, ydata = int(x), int(y)
            xxRA, yyRA = self.ax1_rulerAnchorsItem.getData()
            if self.isCtrlDown:
                ydata = yyRA[0]
            self.ax1_rulerPlotItem.setData([xxRA[0], xdata], [yyRA[0], ydata])

        if not event.isExit():
            x, y = event.pos()
            xdata, ydata = int(x), int(y)
            _img = self.img1.image
            Y, X = _img.shape[:2]
            if xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y:
                ID = self.currentLab2D[ydata, xdata]
                self.updatePropsWidget(ID)
                hoverText = self.hoverValuesFormatted(xdata, ydata)
                self.wcLabel.setText(hoverText)
        else:
            self.clickedOnBud = False
            self.BudMothTempLine.setData([], [])
            self.wcLabel.setText('')
        
        if cursorsInfo['setKeepObjCursor']:
            x, y = event.pos()
            self.highlightHoverIDsKeptObj(x, y)
        
        if cursorsInfo['setManualTrackingCursor']:
            x, y = event.pos()
            # self.highlightHoverID(x, y)
            if event.isExit():
                self.clearGhost()
            else:
                self.drawManualTrackingGhost(x, y)

        setMoveLabelCursor = cursorsInfo['setMoveLabelCursor']
        setExpandLabelCursor = cursorsInfo['setExpandLabelCursor']
        if setMoveLabelCursor or setExpandLabelCursor:
            x, y = event.pos()
            self.updateHoverLabelCursor(x, y)

        # Draw eraser circle
        if cursorsInfo['setEraserCursor']:
            x, y = event.pos()
            self.updateEraserCursor(x, y)
            self.hideItemsHoverBrush(xy=(x, y))
        else:
            self.setHoverToolSymbolData(
                [], [], (self.ax1_EraserCircle, self.ax2_EraserCircle,
                         self.ax1_EraserX, self.ax2_EraserX)
            )

        # Draw Brush circle
        if cursorsInfo['setBrushCursor']:
            x, y = event.pos()
            self.updateBrushCursor(x, y)
            self.hideItemsHoverBrush(xy=(x, y))
        else:
            self.setHoverToolSymbolData(
                [], [], (self.ax2_BrushCircle, self.ax1_BrushCircle),
            )
        
        # Draw label ROi circular cursor
        setLabelRoiCircCursor = cursorsInfo['setLabelRoiCircCursor']
        if setLabelRoiCircCursor:
            x, y = event.pos()
        else:
            x, y = None, None
        self.updateLabelRoiCircularCursor(x, y, setLabelRoiCircCursor)

        drawMothBudLine = (
            self.assignBudMothButton.isChecked() and self.clickedOnBud
            and not event.isExit()
        )
        if drawMothBudLine:
            x, y = event.pos()
            y2, x2 = y, x
            xdata, ydata = int(x), int(y)
            y1, x1 = self.yClickBud, self.xClickBud
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                self.BudMothTempLine.setData([x1, x2], [y1, y2])
            else:
                obj_idx = posData.IDs.index(ID)
                obj = posData.rp[obj_idx]
                y2, x2 = self.getObjCentroid(obj.centroid)
                self.BudMothTempLine.setData([x1, x2], [y1, y2])

        # Temporarily draw spline curve
        # see https://stackoverflow.com/questions/33962717/interpolating-a-closed-curve-using-scipy
        drawSpline = (
            self.curvToolButton.isChecked() and self.splineHoverON
            and not event.isExit()
        )
        if drawSpline:
            x, y = event.pos()
            xx, yy = self.curvAnchors.getData()
            hoverAnchors = self.curvAnchors.pointsAt(event.pos())
            per=False
            # If we are hovering the starting point we generate
            # a closed spline
            if len(xx) >= 2:
                if len(hoverAnchors)>0:
                    xA_hover, yA_hover = hoverAnchors[0].pos()
                    if xx[0]==xA_hover and yy[0]==yA_hover:
                        per=True
                if per:
                    # Append start coords and close spline
                    xx = np.r_[xx, xx[0]]
                    yy = np.r_[yy, yy[0]]
                    xi, yi = self.getSpline(xx, yy, per=per)
                    # self.curvPlotItem.setData([], [])
                else:
                    # Append mouse coords
                    xx = np.r_[xx, x]
                    yy = np.r_[yy, y]
                    xi, yi = self.getSpline(xx, yy, per=per)
                self.curvHoverPlotItem.setData(xi, yi)
        
        setMirroredCursor = (
            self.app.overrideCursor() is None and not event.isExit()
            and isHoverImg1
        )
        if setMirroredCursor:
            x, y = event.pos()
            self.ax2_cursor.setData([x], [y])
        return cursorsInfo
        
    def gui_add_ax_cursors(self):
        try:
            self.ax1.removeItem(self.ax1_cursor)
            self.ax2.removeItem(self.ax2_cursor)
        except Exception as e:
            pass

        self.ax2_cursor = pg.ScatterPlotItem(
            symbol='+', pxMode=True, pen=pg.mkPen('k', width=1),
            brush=pg.mkBrush('w'), size=16, tip=None
        )
        self.ax2.addItem(self.ax2_cursor)

        self.ax1_cursor = pg.ScatterPlotItem(
            symbol='+', pxMode=True, pen=pg.mkPen('k', width=1),
            brush=pg.mkBrush('w'), size=16, tip=None
        )
        self.ax1.addItem(self.ax1_cursor)

    def gui_setCursor(self, modifiers, event):
        noModifier = modifiers == Qt.NoModifier
        shift = modifiers == Qt.ShiftModifier
        ctrl = modifiers == Qt.ControlModifier
        alt = modifiers == Qt.AltModifier
        
        # Alt key was released --> restore cursor
        if self.app.overrideCursor() == Qt.SizeAllCursor and noModifier:
            self.app.restoreOverrideCursor()

        setBrushCursor = (
            self.brushButton.isChecked() and not event.isExit()
            and (noModifier or shift or ctrl)
        )
        setEraserCursor = (
            self.eraserButton.isChecked() and not event.isExit()
            and noModifier
        )
        setAddDelPolyLineCursor = (
            self.addDelPolyLineRoiAction.isChecked() and not event.isExit()
            and noModifier
        )
        setLabelRoiCircCursor = (
            self.labelRoiButton.isChecked() and not event.isExit()
            and (noModifier or shift or ctrl)
            and self.labelRoiIsCircularRadioButton.isChecked()
        )
        setWandCursor = (
            self.wandToolButton.isChecked() and not event.isExit()
            and noModifier
        )
        setLabelRoiCursor = (
            self.labelRoiButton.isChecked() and not event.isExit()
            and noModifier
        )
        setMoveLabelCursor = (
            self.moveLabelToolButton.isChecked() and not event.isExit()
            and noModifier
        )
        setExpandLabelCursor = (
            self.expandLabelToolButton.isChecked() and not event.isExit()
            and noModifier
        )
        setCurvCursor = (
            self.curvToolButton.isChecked() and not event.isExit()
            and noModifier
        )
        setKeepObjCursor = (
            self.keepIDsButton.isChecked() and not event.isExit()
            and noModifier
        )
        setCustomAnnotCursor = (
            self.customAnnotButton is not None and not event.isExit()
            and noModifier
        )
        setManualTrackingCursor = (
            self.manualTrackingButton.isChecked() and not event.isExit()
            and noModifier
        )
        setAddPointCursor = (
            self.pointsLayersToolbar.isVisible() and not event.isExit()
            and noModifier
        )
        overrideCursor = self.app.overrideCursor()
        setPanImageCursor = alt and not event.isExit()
        if setPanImageCursor and overrideCursor is None:
            self.app.setOverrideCursor(Qt.SizeAllCursor)
        elif setBrushCursor or setEraserCursor or setLabelRoiCircCursor:
            self.app.setOverrideCursor(Qt.CrossCursor)
        elif setWandCursor and overrideCursor is None:
            self.app.setOverrideCursor(self.wandCursor)
        elif setLabelRoiCursor and overrideCursor is None:
            self.app.setOverrideCursor(Qt.CrossCursor)
        elif setCurvCursor and overrideCursor is None:
            self.app.setOverrideCursor(self.curvCursor)
        elif setCustomAnnotCursor and overrideCursor is None:
            self.app.setOverrideCursor(Qt.PointingHandCursor)
        elif setAddDelPolyLineCursor:
            self.app.setOverrideCursor(self.polyLineRoiCursor)
        elif setCustomAnnotCursor:
            x, y = event.pos()
            self.highlightHoverID(x, y)        
        elif setKeepObjCursor and overrideCursor is None:
            self.app.setOverrideCursor(Qt.PointingHandCursor)        
        elif setManualTrackingCursor and overrideCursor is None:
            self.app.setOverrideCursor(Qt.PointingHandCursor)
        elif setAddPointCursor:
            self.app.setOverrideCursor(self.addPointsCursor)
        
        return {
            'setBrushCursor': setBrushCursor,
            'setEraserCursor': setEraserCursor,
            'setAddDelPolyLineCursor': setAddDelPolyLineCursor,
            'setLabelRoiCircCursor': setLabelRoiCircCursor,
            'setWandCursor': setWandCursor,
            'setLabelRoiCursor': setLabelRoiCursor,
            'setMoveLabelCursor': setMoveLabelCursor,
            'setExpandLabelCursor': setExpandLabelCursor,
            'setCurvCursor': setCurvCursor,
            'setKeepObjCursor': setKeepObjCursor,
            'setCustomAnnotCursor': setCustomAnnotCursor,
            'setManualTrackingCursor': setManualTrackingCursor,
            'setAddPointCursor': setAddPointCursor,
        }
    
    def gui_hoverEventImg2(self, event):
        try:
            posData = self.data[self.pos_i]
        except AttributeError:
            return
            
        if not event.isExit():
            self.xHoverImg, self.yHoverImg = event.pos()
        else:
            self.xHoverImg, self.yHoverImg = None, None

        # Cursor left image --> restore cursor
        if event.isExit() and self.app.overrideCursor() is not None:
            while self.app.overrideCursor() is not None:
                self.app.restoreOverrideCursor()

        # Alt key was released --> restore cursor
        modifiers = QGuiApplication.keyboardModifiers()
        noModifier = modifiers == Qt.NoModifier
        shift = modifiers == Qt.ShiftModifier
        ctrl = modifiers == Qt.ControlModifier
        if self.app.overrideCursor() == Qt.SizeAllCursor and noModifier:
            self.app.restoreOverrideCursor()

        setBrushCursor = (
            self.brushButton.isChecked() and not event.isExit()
            and (noModifier or shift or ctrl)
        )
        setEraserCursor = (
            self.eraserButton.isChecked() and not event.isExit()
            and noModifier
        )
        setLabelRoiCircCursor = (
            self.labelRoiButton.isChecked() and not event.isExit()
            and (noModifier or shift or ctrl)
            and self.labelRoiIsCircularRadioButton.isChecked()
        )
        if setBrushCursor or setEraserCursor or setLabelRoiCircCursor:
            self.app.setOverrideCursor(Qt.CrossCursor)

        setMoveLabelCursor = (
            self.moveLabelToolButton.isChecked() and not event.isExit()
            and noModifier
        )

        setExpandLabelCursor = (
            self.expandLabelToolButton.isChecked() and not event.isExit()
            and noModifier
        )

        # Cursor is moving on image while Alt key is pressed --> pan cursor
        alt = QGuiApplication.keyboardModifiers() == Qt.AltModifier
        setPanImageCursor = alt and not event.isExit()
        if setPanImageCursor and self.app.overrideCursor() is None:
            self.app.setOverrideCursor(Qt.SizeAllCursor)
        
        setKeepObjCursor = (
            self.keepIDsButton.isChecked() and not event.isExit()
            and noModifier
        )
        if setKeepObjCursor and self.app.overrideCursor() is None:
            self.app.setOverrideCursor(Qt.PointingHandCursor)

        # Update x, y, value label bottom right
        if not event.isExit():
            x, y = event.pos()
            xdata, ydata = int(x), int(y)
            _img = self.currentLab2D
            Y, X = _img.shape                
            # hoverText = self.hoverValuesFormatted(xdata, ydata)
            # self.wcLabel.setText(hoverText)
        else:
            if self.eraserButton.isChecked() or self.brushButton.isChecked():
                self.gui_mouseReleaseEventImg2(event)
            self.wcLabel.setText(f'')

        if setMoveLabelCursor or setExpandLabelCursor:
            x, y = event.pos()
            self.updateHoverLabelCursor(x, y)
        
        if setKeepObjCursor:
            x, y = event.pos()
            self.highlightHoverIDsKeptObj(x, y)

        # Draw eraser circle
        if setEraserCursor:
            x, y = event.pos()
            self.updateEraserCursor(x, y)
        else:
            self.setHoverToolSymbolData(
                [], [], (self.ax1_EraserCircle, self.ax2_EraserCircle,
                         self.ax1_EraserX, self.ax2_EraserX)
            )

        # Draw Brush circle
        if setBrushCursor:
            x, y = event.pos()
            self.updateBrushCursor(x, y)
        else:
            self.setHoverToolSymbolData(
                [], [], (self.ax2_BrushCircle, self.ax1_BrushCircle),
            )
        
        # Draw label ROi circular cursor
        if setLabelRoiCircCursor:
            x, y = event.pos()
        else:
            x, y = None, None
        self.updateLabelRoiCircularCursor(x, y, setLabelRoiCircCursor)
    
    def gui_imgGradShowContextMenu(self, event):
        try:
            # Convert QPointF to QPoint
            self.imgGrad.gradient.menu.popup(event.screenPos().toPoint())
        except AttributeError:
            self.imgGrad.gradient.menu.popup(event.screenPos())
    
    def gui_rightImageShowContextMenu(self, event):
        try:
            # Convert QPointF to QPoint
            self.imgGradRight.gradient.menu.popup(event.screenPos().toPoint())
        except AttributeError:
            self.imgGradRight.gradient.menu.popup(event.screenPos())

    @exception_handler
    def gui_mouseDragEventImg2(self, event):
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        if mode == 'Viewer':
            return

        Y, X = self.get_2Dlab(posData.lab).shape
        x, y = event.pos().x(), event.pos().y()
        xdata, ydata = int(x), int(y)
        if not myutils.is_in_bounds(xdata, ydata, X, Y):
            return

        # Eraser dragging mouse --> keep erasing
        if self.isMouseDragImg2 and self.eraserButton.isChecked():
            posData = self.data[self.pos_i]
            lab_2D = self.get_2Dlab(posData.lab)
            Y, X = lab_2D.shape
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            brushSize = self.brushSizeSpinbox.value()
            rrPoly, ccPoly = self.getPolygonBrush((y, x), Y, X)

            ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)

            # Build eraser mask
            mask = np.zeros(lab_2D.shape, bool)
            mask[ymin:ymax, xmin:xmax][diskMask] = True
            mask[rrPoly, ccPoly] = True

            if self.eraseOnlyOneID:
                mask[lab_2D!=self.erasedID] = False
                self.setHoverToolSymbolColor(
                    xdata, ydata, self.eraserCirclePen,
                    (self.ax2_EraserCircle, self.ax1_EraserCircle),
                    self.eraserButton, hoverRGB=self.img2.lut[self.erasedID],
                    ID=self.erasedID
                )

            self.erasedIDs.extend(lab_2D[mask])

            self.applyEraserMask(mask)
            self.setImageImg2(updateLookuptable=False)

        # Brush paint dragging mouse --> keep painting
        if self.isMouseDragImg2 and self.brushButton.isChecked():
            posData = self.data[self.pos_i]
            lab_2D = self.get_2Dlab(posData.lab)
            Y, X = lab_2D.shape
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)

            ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)
            rrPoly, ccPoly = self.getPolygonBrush((y, x), Y, X)

            # Build brush mask
            mask = np.zeros(lab_2D.shape, bool)
            mask[ymin:ymax, xmin:xmax][diskMask] = True
            mask[rrPoly, ccPoly] = True

            # If user double-pressed 'b' then draw over the labels
            color = self.brushButton.palette().button().color().name()
            if color != self.doublePressKeyButtonColor:
                mask[lab_2D!=0] = False
                self.setHoverToolSymbolColor(
                    xdata, ydata, self.ax2_BrushCirclePen,
                    (self.ax2_BrushCircle, self.ax1_BrushCircle),
                    self.eraserButton, brush=self.ax2_BrushCircleBrush
                )

            # Apply brush mask
            self.applyBrushMask(mask, self.ax2BrushID)

            self.setImageImg2()

        # Move label dragging mouse --> keep moving
        elif self.isMovingLabel and self.moveLabelToolButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            self.dragLabel(x, y)

    @exception_handler
    def gui_mouseReleaseEventImg2(self, event):
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        if mode == 'Viewer':
            return

        Y, X = self.get_2Dlab(posData.lab).shape
        try:
            x, y = event.pos().x(), event.pos().y()
        except Exception as e:
            return
            
        xdata, ydata = int(x), int(y)
        if not myutils.is_in_bounds(xdata, ydata, X, Y):
            self.isMouseDragImg2 = False
            self.updateAllImages()
            return

        # Eraser mouse release --> update IDs and contours
        if self.isMouseDragImg2 and self.eraserButton.isChecked():
            self.isMouseDragImg2 = False
            erasedIDs = np.unique(self.erasedIDs)

            # Update data (rp, etc)
            self.update_rp()

            for ID in erasedIDs:
                if ID not in posData.lab:
                    if self.isSnapshot:
                        self.fixCcaDfAfterEdit('Delete ID with eraser')
                        self.updateAllImages()
                    else:
                        self.warnEditingWithCca_df('Delete ID with eraser')
                    break

        # Brush button mouse release --> update IDs and contours
        elif self.isMouseDragImg2 and self.brushButton.isChecked():
            self.isMouseDragImg2 = False

            self.update_rp()
            self.fillHolesID(self.ax2BrushID, sender='brush')

            if self.editIDcheckbox.isChecked():
                self.tracking(enforce=True, assign_unique_new_IDs=False)

            # Update images
            if self.isNewID:
                editTxt = 'Add new ID with brush tool'
                if self.isSnapshot:
                    self.fixCcaDfAfterEdit(editTxt)
                    self.updateAllImages()
                else:
                    self.warnEditingWithCca_df(editTxt)
            else:
                self.updateAllImages()

        # Move label mouse released, update move
        elif self.isMovingLabel and self.moveLabelToolButton.isChecked():
            self.isMovingLabel = False

            # Update data (rp, etc)
            self.update_rp()

            # Repeat tracking
            self.tracking(enforce=True, assign_unique_new_IDs=False)

            self.updateAllImages()

            if not self.moveLabelToolButton.findChild(QAction).isChecked():
                self.moveLabelToolButton.setChecked(False)

        # Merge IDs
        elif self.mergeIDsButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                mergeID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter ID that you want to merge with ID '
                         f'{self.firstID}',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                mergeID_prompt.exec_()
                if mergeID_prompt.cancel:
                    return
                else:
                    ID = mergeID_prompt.EntryID

            posData.lab[posData.lab==ID] = self.firstID

            # Update data (rp, etc)
            self.update_rp()

            # Repeat tracking
            self.tracking(
                enforce=True, assign_unique_new_IDs=False,
                separateByLabel=False
            )

            if self.isSnapshot:
                self.fixCcaDfAfterEdit('Merge IDs')
                self.updateAllImages()
            else:
                self.warnEditingWithCca_df('Merge IDs')

            if not self.mergeIDsButton.findChild(QAction).isChecked():
                self.mergeIDsButton.setChecked(False)
            self.store_data()

    @exception_handler
    def gui_mouseReleaseEventImg1(self, event):
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        if mode == 'Viewer':
            return

        Y, X = self.get_2Dlab(posData.lab).shape
        x, y = event.pos().x(), event.pos().y()
        xdata, ydata = int(x), int(y)
        if not myutils.is_in_bounds(xdata, ydata, X, Y):
            self.isMouseDragImg2 = False
            self.updateAllImages()
            return

        if mode=='Segmentation and Tracking' or self.isSnapshot:
            # Allow right-click actions on both images
            self.gui_mouseReleaseEventImg2(event)

        # Right-click curvature tool mouse release
        if self.isRightClickDragImg1 and self.curvToolButton.isChecked():
            self.isRightClickDragImg1 = False
            try:
                self.splineToObj(isRightClick=True)
                self.update_rp()
                self.tracking(enforce=True, assign_unique_new_IDs=False)
                if self.isSnapshot:
                    self.fixCcaDfAfterEdit('Add new ID with curvature tool')
                    self.updateAllImages()
                else:
                    self.warnEditingWithCca_df('Add new ID with curvature tool')
                self.clearCurvItems()
                self.curvTool_cb(True)
            except ValueError:
                self.clearCurvItems()
                self.curvTool_cb(True)
                pass

        # Eraser mouse release --> update IDs and contours
        elif self.isMouseDragImg1 and self.eraserButton.isChecked():
            self.isMouseDragImg1 = False

            self.tempLayerImg1.setImage(self.emptyLab)

            erasedIDs = np.unique(self.erasedIDs)

            # Update data (rp, etc)
            self.update_rp()

            for ID in erasedIDs:
                if ID not in posData.IDs:
                    if self.isSnapshot:
                        self.fixCcaDfAfterEdit('Delete ID with eraser')
                        self.updateAllImages()
                    else:
                        self.warnEditingWithCca_df('Delete ID with eraser')
                    break
            else:
                self.updateAllImages()

        # Brush button mouse release
        elif self.isMouseDragImg1 and self.brushButton.isChecked():
            self.isMouseDragImg1 = False

            self.tempLayerImg1.setImage(self.emptyLab)

            # Update data (rp, etc)
            self.update_rp()
            
            posData = self.data[self.pos_i]
            self.fillHolesID(posData.brushID, sender='brush')

            # Repeat tracking
            if self.editIDcheckbox.isChecked():
                self.tracking(enforce=True, assign_unique_new_IDs=False)

            # Update images
            if self.isNewID:
                editTxt = 'Add new ID with brush tool'
                if self.isSnapshot:
                    self.fixCcaDfAfterEdit(editTxt)
                    self.updateAllImages()
                else:
                    self.warnEditingWithCca_df(editTxt)
            else:
                self.updateAllImages()
            
            self.isNewID = False

        # Wand tool release, add new object
        elif self.isMouseDragImg1 and self.wandToolButton.isChecked():
            self.isMouseDragImg1 = False

            self.tempLayerImg1.setImage(self.emptyLab)

            posData = self.data[self.pos_i]
            posData.lab[self.flood_mask] = posData.brushID

            # Update data (rp, etc)
            self.update_rp()

            # Repeat tracking
            self.tracking(enforce=True, assign_unique_new_IDs=False)

            if self.isSnapshot:
                self.fixCcaDfAfterEdit('Add new ID with magic-wand')
                self.updateAllImages()
            else:
                self.warnEditingWithCca_df('Add new ID with magic-wand')
        
        # Label ROI mouse release --> label the ROI with labelRoiWorker
        elif self.isMouseDragImg1 and self.labelRoiButton.isChecked():
            self.labelRoiRunning = True
            self.app.setOverrideCursor(Qt.WaitCursor)
            self.isMouseDragImg1 = False

            if self.labelRoiIsFreeHandRadioButton.isChecked():
                self.freeRoiItem.closeCurve()
            
            proceed = self.labelRoiCheckStartStopFrame()
            if not proceed:
                self.labelRoiCancelled()
                return

            roiImg, self.labelRoiSlice = self.getLabelRoiImage()

            if roiImg.size == 0:
                self.labelRoiCancelled()
                return

            if self.labelRoiModel is None:
                cancel = self.initLabelRoiModel()
                if cancel:
                    self.labelRoiCancelled()
                    return
            
            roiSecondChannel = None
            if self.secondChannelName is not None:
                secondChannelData = self.getSecondChannelData()
                roiSecondChannel = secondChannelData[self.labelRoiSlice]
            
            isTimelapse = self.labelRoiTrangeCheckbox.isChecked()
            if isTimelapse:
                start_n = self.labelRoiStartFrameNoSpinbox.value()
                stop_n = self.labelRoiStopFrameNoSpinbox.value()
                self.progressWin = apps.QDialogWorkerProgress(
                    title='ROI segmentation', parent=self,
                    pbarDesc=f'Segmenting frames n. {start_n} to {stop_n}...'
                )
                self.progressWin.show(self.app)
                self.progressWin.mainPbar.setMaximum(stop_n-start_n)                

            self.app.restoreOverrideCursor() 
            labelRoiWorker = self.labelRoiActiveWorkers[-1]
            labelRoiWorker.start(
                roiImg, roiSecondChannel=roiSecondChannel, 
                isTimelapse=isTimelapse
            )            
            self.app.setOverrideCursor(Qt.WaitCursor)
            self.logger.info(
                f'Magic labeller started on image ROI = {self.labelRoiSlice}...'
            )

        # Move label mouse released, update move
        elif self.isMovingLabel and self.moveLabelToolButton.isChecked():
            self.isMovingLabel = False

            # Update data (rp, etc)
            self.update_rp()

            # Repeat tracking
            self.tracking(enforce=True, assign_unique_new_IDs=False)

            if not self.moveLabelToolButton.findChild(QAction).isChecked():
                self.moveLabelToolButton.setChecked(False)
            else:
                self.updateAllImages()

        # Assign mother to bud
        elif self.assignBudMothButton.isChecked() and self.clickedOnBud:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == self.get_2Dlab(posData.lab)[self.yClickBud, self.xClickBud]:
                return

            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                mothID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter ID that you want to annotate as mother cell',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                mothID_prompt.exec_()
                if mothID_prompt.cancel:
                    return
                else:
                    ID = mothID_prompt.EntryID
                    obj_idx = posData.IDs.index(ID)
                    y, x = posData.rp[obj_idx].centroid
                    xdata, ydata = int(x), int(y)

            if self.isSnapshot:
                # Store undo state before modifying stuff
                self.storeUndoRedoStates(False)

            relationship = posData.cca_df.at[ID, 'relationship']
            ccs = posData.cca_df.at[ID, 'cell_cycle_stage']
            is_history_known = posData.cca_df.at[ID, 'is_history_known']
            # We allow assiging a cell in G1 as mother only on first frame
            # OR if the history is unknown
            if relationship == 'bud' and posData.frame_i > 0 and is_history_known:
                self.assignBudMothButton.setChecked(False)
                txt = html_utils.paragraph(
                    f'You clicked on <b>ID {ID}</b> which is a <b>BUD</b>.<br>'
                    'To assign a bud to a cell <b>start by clicking on a bud</b> '
                    'and release on a cell in G1'
                )
                msg = widgets.myMessageBox()
                msg.critical(
                    self, 'Released on a bud', txt
                )
                self.assignBudMothButton.setChecked(True)
                return

            elif ccs != 'G1' and posData.frame_i > 0:
                self.assignBudMothButton.setChecked(False)
                txt = html_utils.paragraph(
                    f'You clicked on <b>ID={ID}</b> which is <b>NOT in G1</b>.<br>'
                    'To assign a bud to a cell start by clicking on a bud '
                    'and release on a cell in G1'
                )
                msg = widgets.myMessageBox()
                msg.critical(
                    self, 'Released on a cell NOT in G1', txt
                )
                self.assignBudMothButton.setChecked(True)
                return

            elif posData.frame_i == 0:
                # Check that clicked bud actually is smaller that mother
                # otherwise warn the user that he might have clicked first
                # on a mother
                budID = self.get_2Dlab(posData.lab)[self.yClickBud, self.xClickBud]
                new_mothID = self.get_2Dlab(posData.lab)[ydata, xdata]
                bud_obj_idx = posData.IDs.index(budID)
                new_moth_obj_idx = posData.IDs.index(new_mothID)
                rp_budID = posData.rp[bud_obj_idx]
                rp_new_mothID = posData.rp[new_moth_obj_idx]
                if rp_budID.area >= rp_new_mothID.area:
                    self.assignBudMothButton.setChecked(False)
                    msg = widgets.myMessageBox()
                    txt = (
                        f'You clicked FIRST on ID {budID} and then on {new_mothID}.\n'
                        f'For me this means that you want ID {budID} to be the '
                        f'BUD of ID {new_mothID}.\n'
                        f'However <b>ID {budID} is bigger than {new_mothID}</b> '
                        f'so maybe you shoul have clicked FIRST on {new_mothID}?\n\n'
                        'What do you want me to do?'
                    )
                    txt = html_utils.paragraph(txt)
                    swapButton, keepButton = msg.warning(
                        self, 'Which one is bud?', txt,
                        buttonsTexts=(
                            f'Assign ID {new_mothID} as the bud of ID {budID}',
                            f'Keep ID {budID} as the bud of  ID {new_mothID}'
                        )
                    )
                    if msg.clickedButton == swapButton:
                        (xdata, ydata,
                        self.xClickBud, self.yClickBud) = (
                            self.xClickBud, self.yClickBud,
                            xdata, ydata
                        )
                    self.assignBudMothButton.setChecked(True)

            elif is_history_known and not self.clickedOnHistoryKnown:
                self.assignBudMothButton.setChecked(False)
                budID = self.get_2Dlab(posData.lab)[ydata, xdata]
                # Allow assigning an unknown cell ONLY to another unknown cell
                txt = (
                    f'You started by clicking on ID {budID} which has '
                    'UNKNOWN history, but you then clicked/released on '
                    f'ID {ID} which has KNOWN history.\n\n'
                    'Only two cells with UNKNOWN history can be assigned as '
                    'relative of each other.'
                )
                msg = QMessageBox()
                msg.critical(
                    self, 'Released on a cell with KNOWN history', txt, msg.Ok
                )
                self.assignBudMothButton.setChecked(True)
                return

            self.clickedOnHistoryKnown = is_history_known
            self.xClickMoth, self.yClickMoth = xdata, ydata
            self.assignBudMoth()

            if not self.assignBudMothButton.findChild(QAction).isChecked():
                self.assignBudMothButton.setChecked(False)

            self.clickedOnBud = False
            self.BudMothTempLine.setData([], [])

    def gui_clickedDelRoi(self, event, left_click, right_click):
        posData = self.data[self.pos_i]
        x, y = event.pos().x(), event.pos().y()

        # Check if right click on ROI
        delROIs = (
            posData.allData_li[posData.frame_i]['delROIs_info']['rois'].copy()
        )
        for r, roi in enumerate(delROIs):
            ROImask = self.getDelRoiMask(roi)
            if self.isSegm3D:
                clickedOnROI = ROImask[self.z_lab(), int(y), int(x)]
            else:
                clickedOnROI = ROImask[int(y), int(x)]
            raiseContextMenuRoi = right_click and clickedOnROI
            dragRoi = left_click and clickedOnROI
            if raiseContextMenuRoi:
                self.roi_to_del = roi
                self.roiContextMenu = QMenu(self)
                separator = QAction(self)
                separator.setSeparator(True)
                self.roiContextMenu.addAction(separator)
                action = QAction('Remove ROI')
                action.triggered.connect(self.removeDelROI)
                self.roiContextMenu.addAction(action)
                try:
                    # Convert QPointF to QPoint
                    self.roiContextMenu.exec_(event.screenPos().toPoint())
                except AttributeError:
                    self.roiContextMenu.exec_(event.screenPos())
                return True
            elif dragRoi:
                event.ignore()
                return True
        return False

    def gui_getHoveredSegmentsPolyLineRoi(self):
        posData = self.data[self.pos_i]
        delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
        segments = []
        for roi in delROIs_info['rois']:
            if not isinstance(roi, pg.PolyLineROI):
                continue       
            for seg in roi.segments:       
                if seg.currentPen == seg.hoverPen:
                    seg.roi = roi
                    segments.append(seg)
        return segments
    
    def gui_getHoveredHandlesPolyLineRoi(self):
        posData = self.data[self.pos_i]
        delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
        handles = []
        for roi in delROIs_info['rois']:
            if not isinstance(roi, pg.PolyLineROI):
                continue           
            for handle in roi.getHandles():       
                if handle.currentPen == handle.hoverPen:
                    handle.roi = roi
                    handles.append(handle)
        return handles
    
    @exception_handler
    def gui_mousePressRightImage(self, event):
        modifiers = QGuiApplication.keyboardModifiers()
        ctrl = modifiers == Qt.ControlModifier
        alt = modifiers == Qt.AltModifier
        isMod = alt
        right_click = event.button() == Qt.MouseButton.RightButton and not isMod
        is_right_click_action_ON = any([
            b.isChecked() for b in self.checkableQButtonsGroup.buttons()
        ])
        self.typingEditID = False
        showLabelsGradMenu = right_click and not is_right_click_action_ON
        if showLabelsGradMenu:
            self.gui_rightImageShowContextMenu(event)
            event.ignore()
        else: 
            self.gui_mousePressEventImg1(event)

    @exception_handler
    def gui_mouseDragRightImage(self, event):
        self.gui_mouseDragEventImg1(event)

    @exception_handler
    def gui_mouseReleaseRightImage(self, event):
        self.gui_mouseReleaseEventImg1(event)

    @exception_handler
    def gui_mousePressEventImg1(self, event):
        self.typingEditID = False
        modifiers = QGuiApplication.keyboardModifiers()
        ctrl = modifiers == Qt.ControlModifier
        alt = modifiers == Qt.AltModifier
        isMod = alt
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        isCcaMode = mode == 'Cell cycle analysis'
        isCustomAnnotMode = mode == 'Custom annotations'
        left_click = event.button() == Qt.MouseButton.LeftButton and not isMod
        middle_click = self.isMiddleClick(event, modifiers)
        right_click = event.button() == Qt.MouseButton.RightButton
        isPanImageClick = self.isPanImageClick(event, modifiers)
        brushON = self.brushButton.isChecked()
        curvToolON = self.curvToolButton.isChecked()
        histON = self.setIsHistoryKnownButton.isChecked()
        eraserON = self.eraserButton.isChecked()
        rulerON = self.rulerButton.isChecked()
        wandON = self.wandToolButton.isChecked() and not isPanImageClick
        polyLineRoiON = self.addDelPolyLineRoiAction.isChecked()
        labelRoiON = self.labelRoiButton.isChecked()
        keepObjON = self.keepIDsButton.isChecked()
        separateON = self.separateBudButton.isChecked()
        addPointsByClickingButton = self.buttonAddPointsByClickingActive()

        # Check if right-click on segment of polyline roi to add segment
        segments = self.gui_getHoveredSegmentsPolyLineRoi()
        if len(segments) == 1 and right_click:
            seg = segments[0]
            seg.roi.segmentClicked(seg, event)
            return
        
        # Check if right-click on handle of polyline roi to remove it
        handles = self.gui_getHoveredHandlesPolyLineRoi()
        if len(handles) == 1 and right_click:
            handle = handles[0]
            handle.roi.removeHandle(handle)
            return

        # Check if click on ROI
        isClickOnDelRoi = self.gui_clickedDelRoi(event, left_click, right_click)
        if isClickOnDelRoi:
            return
        
        dragImgLeft = (
            left_click and not brushON and not histON
            and not curvToolON and not eraserON and not rulerON
            and not wandON and not polyLineRoiON and not labelRoiON
            and not middle_click and not keepObjON and not separateON
            and addPointsByClickingButton is None
        )
        if isPanImageClick:
            dragImgLeft = True

        is_right_click_custom_ON = any([
            b.isChecked() for b in self.customAnnotDict.keys()
        ])

        canAnnotateDivision = (
             not self.assignBudMothButton.isChecked()
             and not self.setIsHistoryKnownButton.isChecked()
             and not self.curvToolButton.isChecked()
             and not is_right_click_custom_ON
             and not labelRoiON
             and not separateON
        )

        # In timelapse mode division can be annotated if isCcaMode and right-click
        # while in snapshot mode with Ctrl+right-click
        isAnnotateDivision = (
            (right_click and isCcaMode and canAnnotateDivision)
            or (right_click and ctrl and self.isSnapshot)
        )

        isCustomAnnot = (
            (right_click or dragImgLeft)
            and (isCustomAnnotMode or self.isSnapshot)
            and self.customAnnotButton is not None
        )

        is_right_click_action_ON = any([
            b.isChecked() for b in self.checkableQButtonsGroup.buttons()
        ])

        isOnlyRightClick = (
            right_click and canAnnotateDivision and not isAnnotateDivision
            and not isMod and not is_right_click_action_ON
            and not is_right_click_custom_ON
        )

        if isOnlyRightClick:
            self.gui_imgGradShowContextMenu(event)
            event.ignore()
            return

        # Left click actions
        canCurv = (
            curvToolON and not self.assignBudMothButton.isChecked()
            and not brushON and not dragImgLeft and not eraserON
            and not polyLineRoiON and not labelRoiON
            and addPointsByClickingButton is None
        )
        canBrush = (
            brushON and not curvToolON and not rulerON
            and not dragImgLeft and not eraserON and not wandON
            and not labelRoiON
            and addPointsByClickingButton is None
        )
        canErase = (
            eraserON and not curvToolON and not rulerON
            and not dragImgLeft and not brushON and not wandON
            and not polyLineRoiON and not labelRoiON
            and addPointsByClickingButton is None
        )
        canRuler = (
            rulerON and not curvToolON and not brushON
            and not dragImgLeft and not brushON and not wandON
            and not polyLineRoiON and not labelRoiON
            and addPointsByClickingButton is None
        )
        canWand = (
            wandON and not curvToolON and not brushON
            and not dragImgLeft and not brushON and not rulerON
            and not polyLineRoiON and not labelRoiON
            and addPointsByClickingButton is None
        )
        canPolyLine = (
            polyLineRoiON and not wandON and not curvToolON and not brushON
            and not dragImgLeft and not brushON and not rulerON
            and not labelRoiON
            and addPointsByClickingButton is None
        )
        canLabelRoi = (
            labelRoiON and not wandON and not curvToolON and not brushON
            and not dragImgLeft and not brushON and not rulerON
            and not polyLineRoiON and not keepObjON
            and addPointsByClickingButton is None
        )
        canKeep = (
            keepObjON and not wandON and not curvToolON and not brushON
            and not dragImgLeft and not brushON and not rulerON
            and not polyLineRoiON and not labelRoiON 
            and addPointsByClickingButton is None
        )
        canAddPoint = (
            addPointsByClickingButton is not None and not wandON 
            and not curvToolON and not brushON
            and not dragImgLeft and not brushON and not rulerON
            and not polyLineRoiON and not labelRoiON  and not keepObjON
        )

        # Enable dragging of the image window like pyqtgraph original code
        if dragImgLeft and not isCustomAnnot:
            pg.ImageItem.mousePressEvent(self.img1, event)
            event.ignore()
            return

        if mode == 'Viewer' and not canRuler:
            self.startBlinkingModeCB()
            event.ignore()
            return

        # Allow right-click or middle-click actions on both images
        eventOnImg2 = (
            (right_click or middle_click)
            and (mode=='Segmentation and Tracking' or self.isSnapshot)
            and not isAnnotateDivision
        )
        if eventOnImg2:
            event.isImg1Sender = True
            self.gui_mousePressEventImg2(event)

        x, y = event.pos().x(), event.pos().y()
        xdata, ydata = int(x), int(y)
        Y, X = self.get_2Dlab(posData.lab).shape
        if xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y:
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
        else:
            return

        # Paint new IDs with brush and left click on the left image
        if left_click and canBrush:
            # Store undo state before modifying stuff
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            lab_2D = self.get_2Dlab(posData.lab)
            Y, X = lab_2D.shape
            self.storeUndoRedoStates(False)

            ID = self.getHoverID(xdata, ydata)

            if ID > 0:
                posData.brushID = ID
                self.isNewID = False
            else:
                # Update brush ID. Take care of disappearing cells to remember
                # to not use their IDs anymore in the future
                self.isNewID = True
                self.setBrushID()
                self.updateLookuptable(lenNewLut=posData.brushID+1)

            self.brushColor = self.lut[posData.brushID]/255

            self.yPressAx2, self.xPressAx2 = y, x

            ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)
            diskSlice = (slice(ymin, ymax), slice(xmin, xmax))

            self.isMouseDragImg1 = True

            # Draw new objects
            localLab = lab_2D[diskSlice]
            mask = diskMask.copy()
            if not self.isPowerBrush():
                mask[localLab!=0] = False

            self.applyBrushMask(mask, posData.brushID, toLocalSlice=diskSlice)

            self.setImageImg2(updateLookuptable=False)

            img = self.img1.image.copy()
            how = self.drawIDsContComboBox.currentText()
            lab2D = self.get_2Dlab(posData.lab)
            self.globalBrushMask = np.zeros(lab2D.shape, dtype=bool)
            brushMask = localLab == posData.brushID
            brushMask = np.logical_and(brushMask, diskMask)
            self.setTempImg1Brush(
                True, brushMask, posData.brushID, toLocalSlice=diskSlice
            )

            self.lastHoverID = -1

        elif left_click and canErase:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            lab_2D = self.get_2Dlab(posData.lab)
            Y, X = lab_2D.shape

            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)

            self.yPressAx2, self.xPressAx2 = y, x
            # Keep a list of erased IDs got erased
            self.erasedIDs = []
            
            self.erasedID = self.getHoverID(xdata, ydata)

            ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)

            # Build eraser mask
            mask = np.zeros(lab_2D.shape, bool)
            mask[ymin:ymax, xmin:xmax][diskMask] = True


            # If user double-pressed 'b' then erase over ALL labels
            color = self.eraserButton.palette().button().color().name()
            eraseOnlyOneID = (
                color != self.doublePressKeyButtonColor
                and self.erasedID != 0
            )

            self.eraseOnlyOneID = eraseOnlyOneID

            if eraseOnlyOneID:
                mask[lab_2D!=self.erasedID] = False

            self.getDisplayedImg1()
            self.setTempImg1Eraser(mask, init=True)
            self.applyEraserMask(mask)

            self.erasedIDs.extend(lab_2D[mask])  

            for erasedID in np.unique(self.erasedIDs):
                if erasedID == 0:
                    continue
                self.erasedLab[lab_2D==erasedID] = erasedID
            
            self.isMouseDragImg1 = True

        elif left_click and canAddPoint:
            x, y = event.pos().x(), event.pos().y()
            action = addPointsByClickingButton.action
            hoveredPoints = action.scatterItem.pointsAt(event.pos())
            if hoveredPoints:
                self.removeClickedPoints(action, hoveredPoints)
            else:
                self.addClickedPoint(action, x, y)
            self.drawPointsLayers(computePointsLayers=False)
        
        elif left_click and canRuler or canPolyLine:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            closePolyLine = (
                len(self.startPointPolyLineItem.pointsAt(event.pos())) > 0
            )
            if not self.tempSegmentON or canPolyLine:
                # Keep adding anchor points for polyline
                self.ax1_rulerAnchorsItem.setData([xdata], [ydata])
                self.tempSegmentON = True
            else:
                self.tempSegmentON = False
                xxRA, yyRA = self.ax1_rulerAnchorsItem.getData()
                if self.isCtrlDown:
                    ydata = yyRA[0]
                self.ax1_rulerPlotItem.setData(
                    [xxRA[0], xdata], [yyRA[0], ydata]
                )
                self.ax1_rulerAnchorsItem.setData(
                    [xxRA[0], xdata], [yyRA[0], ydata]
                )
             
            if canPolyLine and not self.startPointPolyLineItem.getData()[0]:
                # Create and add roi item
                self.createDelPolyLineRoi()
                # Add start point of polyline roi
                self.startPointPolyLineItem.setData([xdata], [ydata])
                self.polyLineRoi.points.append((xdata, ydata))
            elif canPolyLine:
                # Add points to polyline roi and eventually close it
                if not closePolyLine:
                    self.polyLineRoi.points.append((xdata, ydata))
                self.addPointsPolyLineRoi(closed=closePolyLine)
                if closePolyLine:
                    # Close polyline ROI
                    if len(self.polyLineRoi.getLocalHandlePositions()) == 2:
                        self.polyLineRoi = self.replacePolyLineRoiWithLineRoi(
                            self.polyLineRoi
                        )
                    self.tempSegmentON = False
                    self.ax1_rulerAnchorsItem.setData([], [])
                    self.ax1_rulerPlotItem.setData([], [])
                    self.startPointPolyLineItem.setData([], [])
                    self.addRoiToDelRoiInfo(self.polyLineRoi)
                    # Call roi moving on closing ROI
                    self.delROImoving(self.polyLineRoi)
                    self.delROImovingFinished(self.polyLineRoi)
        
        elif left_click and canKeep:
            # Right click is passed earlier to gui_mousePressImg2
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                keepID_win = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                        'Enter ID that you want to keep',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                keepID_win.exec_()
                if keepID_win.cancel:
                    return
                else:
                    ID = keepID_win.EntryID
            
            if ID in self.keptObjectsIDs:
                self.keptObjectsIDs.remove(ID)
                self.clearHighlightedText()
            else:
                self.keptObjectsIDs.append(ID)
                self.highlightLabelID(ID)
            
            self.updateTempLayerKeepIDs()

        elif right_click and canCurv:
            # Draw manually assisted auto contour
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            Y, X = self.get_2Dlab(posData.lab).shape

            self.autoCont_x0 = xdata
            self.autoCont_y0 = ydata
            self.xxA_autoCont, self.yyA_autoCont = [], []
            self.curvAnchors.addPoints([x], [y])
            img = self.getDisplayedImg1()
            self.autoContObjMask = np.zeros(img.shape, np.uint8)
            self.isRightClickDragImg1 = True

        elif left_click and canCurv:
            # Draw manual spline
            x, y = event.pos().x(), event.pos().y()
            Y, X = self.get_2Dlab(posData.lab).shape

            # Check if user clicked on starting anchor again --> close spline
            closeSpline = False
            clickedAnchors = self.curvAnchors.pointsAt(event.pos())
            xxA, yyA = self.curvAnchors.getData()
            if len(xxA)>0:
                if len(xxA) == 1:
                    self.splineHoverON = True
                x0, y0 = xxA[0], yyA[0]
                if len(clickedAnchors)>0:
                    xA_clicked, yA_clicked = clickedAnchors[0].pos()
                    if x0==xA_clicked and y0==yA_clicked:
                        x = x0
                        y = y0
                        closeSpline = True

            # Add anchors
            self.curvAnchors.addPoints([x], [y])
            try:
                xx, yy = self.curvHoverPlotItem.getData()
                self.curvPlotItem.setData(xx, yy)
            except Exception as e:
                # traceback.print_exc()
                pass
            
            if closeSpline:
                self.splineHoverON = False
                self.splineToObj()
                self.update_rp()
                self.tracking(enforce=True, assign_unique_new_IDs=False)
                if self.isSnapshot:
                    self.fixCcaDfAfterEdit('Add new ID with curvature tool')
                    self.updateAllImages()
                else:
                    self.warnEditingWithCca_df('Add new ID with curvature tool')
                self.clearCurvItems()
                self.curvTool_cb(True)
            t1 = time.perf_counter()

        elif left_click and canWand:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            Y, X = self.get_2Dlab(posData.lab).shape
            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)

            posData.brushID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if posData.brushID == 0:
                self.setBrushID()
                self.updateLookuptable(
                    lenNewLut=posData.brushID+1
                )
            self.brushColor = self.img2.lut[posData.brushID]/255

            # NOTE: flood is on mousedrag or release
            tol = self.wandToleranceSlider.value()
            self.flood_img = myutils.to_uint8(self.getDisplayedImg1())
            flood_mask = skimage.segmentation.flood(
                self.flood_img, (ydata, xdata), tolerance=tol
            )
            bkgrLabMask = self.get_2Dlab(posData.lab)==0

            drawUnderMask = np.logical_or(
                posData.lab==0, posData.lab==posData.brushID
            )
            self.flood_mask = np.logical_and(flood_mask, drawUnderMask)

            if self.wandAutoFillCheckbox.isChecked():
                self.flood_mask = scipy.ndimage.binary_fill_holes(
                    self.flood_mask
                )

            if np.any(self.flood_mask):
                mask = np.logical_or(
                    self.flood_mask,
                    posData.lab==posData.brushID
                )
                self.setTempImg1Brush(True, mask, posData.brushID)
            self.isMouseDragImg1 = True
        
        elif right_click and self.manualTrackingButton.isChecked():
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            manualTrackID = self.manualTrackingToolbar.spinboxID.value()
            clickedID = self.getClickedID(
                xdata, ydata, text=f'that you want to assign to {manualTrackID}'
            )
            if clickedID is None:
                return

            if clickedID == manualTrackID:
                self.manualTrackingToolbar.showWarning(
                    f'The clicked object already has ID = {manualTrackID}'
                )
                return

            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)

            posData = self.data[self.pos_i]
            currentIDs = posData.IDs.copy()
            if manualTrackID in currentIDs:
                tempID = max(currentIDs) + 1
                posData.lab[posData.lab == clickedID] = tempID
                posData.lab[posData.lab == manualTrackID] = clickedID
                posData.lab[posData.lab == tempID] = manualTrackID
                self.manualTrackingToolbar.showWarning(
                    f'The ID {manualTrackID} already exists --> '
                    f'ID {manualTrackID} has been swapped with {clickedID}'
                )
            else:
                posData.lab[posData.lab == clickedID] = manualTrackID
                self.manualTrackingToolbar.showInfo(
                    f'ID {clickedID} changed to {manualTrackID}.'
                )
            
            self.update_rp()
            self.updateAllImages()

        # Label ROI mouse press
        elif (left_click or right_click) and canLabelRoi:
            if right_click:
                # Force model initialization on mouse release
                self.labelRoiModel = None
            
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)

            if self.labelRoiIsRectRadioButton.isChecked():
                self.labelRoiItem.setPos((xdata, ydata))
            elif self.labelRoiIsFreeHandRadioButton.isChecked():
                self.freeRoiItem.addPoint(xdata, ydata)
            
            self.isMouseDragImg1 = True

        # Annotate cell cycle division
        elif isAnnotateDivision:
            if posData.cca_df is None:
                return

            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                divID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter ID that you want to annotate as divided',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                divID_prompt.exec_()
                if divID_prompt.cancel:
                    return
                else:
                    ID = divID_prompt.EntryID
                    obj_idx = posData.IDs.index(ID)
                    y, x = posData.rp[obj_idx].centroid
                    xdata, ydata = int(x), int(y)

            if not self.isSnapshot:
                # Store undo state before modifying stuff
                self.storeUndoRedoStates(False)
                # Annotate or undo division
                self.manualCellCycleAnnotation(ID)
            else:
                self.undoBudMothAssignment(ID)

        # Assign bud to mother (mouse down on bud)
        elif right_click and self.assignBudMothButton.isChecked():
            if self.clickedOnBud:
                # NOTE: self.clickedOnBud is set to False when assigning a mother
                # is successfull in mouse release event
                # We still have to click on a mother
                return

            if posData.cca_df is None:
                return

            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                budID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter ID of a bud you want to correct mother assignment',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                budID_prompt.exec_()
                if budID_prompt.cancel:
                    return
                else:
                    ID = budID_prompt.EntryID

            obj_idx = posData.IDs.index(ID)
            y, x = posData.rp[obj_idx].centroid
            xdata, ydata = int(x), int(y)

            relationship = posData.cca_df.at[ID, 'relationship']
            is_history_known = posData.cca_df.at[ID, 'is_history_known']
            self.clickedOnHistoryKnown = is_history_known
            # We allow assiging a cell in G1 as bud only on first frame
            # OR if the history is unknown
            if relationship != 'bud' and posData.frame_i > 0 and is_history_known:
                txt = (f'You clicked on ID {ID} which is NOT a bud.\n'
                       'To assign a bud to a cell start by clicking on a bud '
                       'and release on a cell in G1')
                msg = QMessageBox()
                msg.critical(
                    self, 'Not a bud', txt, msg.Ok
                )
                return

            self.clickedOnBud = True
            self.xClickBud, self.yClickBud = xdata, ydata

        # Annotate (or undo) that cell has unknown history
        elif right_click and self.setIsHistoryKnownButton.isChecked():
            if posData.cca_df is None:
                return

            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                unknownID_prompt = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter ID that you want to annotate as '
                         '"history UNKNOWN/KNOWN"',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                unknownID_prompt.exec_()
                if unknownID_prompt.cancel:
                    return
                else:
                    ID = unknownID_prompt.EntryID
                    obj_idx = posData.IDs.index(ID)
                    y, x = posData.rp[obj_idx].centroid
                    xdata, ydata = int(x), int(y)

            self.annotateIsHistoryKnown(ID)
            if not self.setIsHistoryKnownButton.findChild(QAction).isChecked():
                self.setIsHistoryKnownButton.setChecked(False)

        elif isCustomAnnot:
            x, y = event.pos().x(), event.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            if ID == 0:
                nearest_ID = self.nearest_nonzero(
                    self.get_2Dlab(posData.lab), y, x
                )
                clickedBkgrDialog = apps.QLineEditDialog(
                    title='Clicked on background',
                    msg='You clicked on the background.\n'
                         'Enter ID that you want to annotate as divided',
                    parent=self, allowedValues=posData.IDs,
                    defaultTxt=str(nearest_ID)
                )
                clickedBkgrDialog.exec_()
                if clickedBkgrDialog.cancel:
                    return
                else:
                    ID = clickedBkgrDialog.EntryID
                    obj_idx = posData.IDs.index(ID)
                    y, x = posData.rp[obj_idx].centroid
                    xdata, ydata = int(x), int(y)

            button = self.doCustomAnnotation(ID, fromClick=True)
            keepActive = self.customAnnotDict[button]['state']['keepActive']
            if not keepActive:
                button.setChecked(False)

    def gui_addCreatedAxesItems(self):
        self.ax1.addItem(self.ax1_contoursImageItem)
        self.ax1.addItem(self.ax1_oldMothBudLinesItem)
        self.ax1.addItem(self.ax1_newMothBudLinesItem)
        self.ax1.addItem(self.ax1_lostObjScatterItem)
        self.ax1.addItem(self.ccaFailedScatterItem)

        self.ax2.addItem(self.ax2_contoursImageItem)
        self.ax2.addItem(self.ax2_oldMothBudLinesItem)
        self.ax2.addItem(self.ax2_newMothBudLinesItem)
        self.ax2.addItem(self.ax2_lostObjScatterItem)

        self.textAnnot[0].addToPlotItem(self.ax1)
        self.textAnnot[1].addToPlotItem(self.ax2)
    
    def gui_raiseBottomLayoutContextMenu(self, event):
        try:
            # Convert QPointF to QPoint
            self.bottomLayoutContextMenu.popup(event.screenPos().toPoint())
        except AttributeError:
            self.bottomLayoutContextMenu.popup(event.screenPos())
    
    def areContoursRequested(self, ax):
        if ax == 0 and self.annotContourCheckbox.isChecked():
            return True

        if ax == 1:
            if not self.labelsGrad.showRightImgAction.isChecked():
                return False

            isRightDifferentAnnot = self.rightBottomGroupbox.isChecked()
            areContRequestedRight = self.annotContourCheckboxRight.isChecked()
           
            if isRightDifferentAnnot and areContRequestedRight:
                return True
            
            areContRequestedLeft = self.annotContourCheckbox.isChecked()
            if not isRightDifferentAnnot and areContRequestedLeft:
                return True
        return False
    
    def areMothBudLinesRequested(self, ax):
        if ax == 0:
            if self.annotCcaInfoCheckbox.isChecked():
                return True
            if self.drawMothBudLinesCheckbox.isChecked():
                return True
        else:
            if not self.labelsGrad.showRightImgAction.isChecked():
                return False
            
            isRightDifferentAnnot = self.rightBottomGroupbox.isChecked()
            areLinesRequestedRight = self.annotCcaInfoCheckboxRight.isChecked()
            if isRightDifferentAnnot and areLinesRequestedRight:
                return True
        
            areLinesRequestedLeft = self.drawMothBudLinesCheckboxRight.isChecked()
            if not isRightDifferentAnnot and areLinesRequestedLeft:
                return True
        return False
    
    def getMothBudLineScatterItem(self, ax, new):
        if ax == 0:
            if new:
                return self.ax1_newMothBudLinesItem
            else:
                return self.ax1_oldMothBudLinesItem
        else:
            if new:
                return self.ax2_newMothBudLinesItem
            else:
                return self.ax2_oldMothBudLinesItem
    
    def labelRoiIsCircularRadioButtonToggled(self, checked):
        if checked:
            self.labelRoiCircularRadiusSpinbox.setDisabled(False)
        else:
            self.labelRoiCircularRadiusSpinbox.setDisabled(True)
    
    def pxModeToggled(self, checked):
        if self.highLowResToggle.isChecked():
            for ax in range(2):
                self.textAnnot[ax].setPxMode(checked)
        self.df_settings.at['pxMode', 'value'] = int(checked)
        self.updateAllImages()

    def relabelSequentialCallback(self):
        mode = str(self.modeComboBox.currentText())
        if mode == 'Viewer' or mode == 'Cell cycle analysis':
            self.startBlinkingModeCB()
            return
        self.store_data()
        posData = self.data[self.pos_i]
        if posData.SizeT > 1:
            self.progressWin = apps.QDialogWorkerProgress(
                title='Re-labelling sequential', parent=self,
                pbarDesc='Relabelling sequential...'
            )
            self.progressWin.show(self.app)
            self.progressWin.mainPbar.setMaximum(0)
            self.startRelabellingWorker(posData)
        else:
            self.storeUndoRedoStates(False)
            posData.lab, fw, inv = skimage.segmentation.relabel_sequential(
                posData.lab
            )
            # Update annotations based on relabelling
            newIDs = list(inv.in_values)
            oldIDs = list(inv.out_values)
            newIDs.append(-1)
            oldIDs.append(-1)
            self.update_cca_df_relabelling(posData, oldIDs, newIDs)
            self.updateAnnotatedIDs(oldIDs, newIDs, logger=self.logger.info)
            self.store_data()
            self.update_rp()
            li = list(zip(oldIDs, newIDs))
            s = '\n'.join([str(pair).replace(',', ' -->') for pair in li])
            s = f'IDs relabelled as follows:\n{s}'
            self.logger.info(s)
            self.updateAllImages()
    
    def updateAnnotatedIDs(self, oldIDs, newIDs, logger=print):
        logger('Updating annotated IDs...')
        posData = self.data[self.pos_i]

        mapper = dict(zip(oldIDs, newIDs))
        posData.ripIDs = set([mapper[ripID] for ripID in posData.ripIDs])
        posData.binnedIDs = set([mapper[binID] for binID in posData.binnedIDs])
        self.keptObjectsIDs = widgets.KeptObjectIDsList(
            self.keptIDsLineEdit, self.keepIDsConfirmAction
        )

        customAnnotButtons = list(self.customAnnotDict.keys())
        for button in customAnnotButtons:
            customAnnotValues = self.customAnnotDict[button]
            annotatedIDs = customAnnotValues['annotatedIDs'][self.pos_i]
            mappedAnnotIDs = {}
            for frame_i, annotIDs_i in annotatedIDs.items():
                mappedIDs = [mapper[ID] for ID in annotIDs_i]
                mappedAnnotIDs[frame_i] = mappedIDs
            customAnnotValues['annotatedIDs'][self.pos_i] = mappedAnnotIDs

    def storeTrackingAlgo(self, checked):
        if not checked:
            return

        trackingAlgo = self.sender().text()
        self.df_settings.at['tracking_algorithm', 'value'] = trackingAlgo
        self.df_settings.to_csv(self.settings_csv_path)

        if self.sender().text() == 'YeaZ':
            msg = QMessageBox()
            info_txt = html_utils.paragraph(f"""
                Note that YeaZ tracking algorithm tends to be sliglhtly more accurate
                overall, but it is <b>less capable of detecting segmentation
                errors.</b><br><br>
                If you need to correct as many segmentation errors as possible
                we recommend using Cell-ACDC tracking algorithm.
            """)
            msg.information(self, 'Info about YeaZ', info_txt, msg.Ok)
        
        self.initRealTimeTracker()

    def findID(self):
        posData = self.data[self.pos_i]
        searchIDdialog = apps.QLineEditDialog(
            title='Search object by ID',
            msg='Enter object ID to find and highlight',
            parent=self, allowedValues=posData.IDs
        )
        searchIDdialog.exec_()
        if searchIDdialog.cancel:
            return
        self.highlightSearchedID(searchIDdialog.EntryID)
        propsQGBox = self.guiTabControl.propsQGBox
        propsQGBox.idSB.setValue(searchIDdialog.EntryID)

    def workerProgress(self, text, loggerLevel='INFO'):
        if self.progressWin is not None:
            self.progressWin.logConsole.append(text)
        self.logger.log(getattr(logging, loggerLevel), text)

    def workerFinished(self):
        if self.progressWin is not None:
            self.progressWin.workerFinished = True
            self.progressWin.close()
            self.progressWin = None
        self.logger.info('Worker process ended.')
        self.updateAllImages()
        self.titleLabel.setText('Done', color='w')
    
    def loadingNewChunk(self, chunk_range):
        coord0_chunk, coord1_chunk = chunk_range
        desc = (
            f'Loading new window, range = ({coord0_chunk}, {coord1_chunk})...'
        )
        self.progressWin = apps.QDialogWorkerProgress(
            title='Loading data...', parent=self, pbarDesc=desc
        )
        self.progressWin.mainPbar.setMaximum(0)
        self.progressWin.show(self.app)
    
    def lazyLoaderFinished(self):
        self.logger.info('Load chunk data worker done.')
        if self.lazyLoader.updateImgOnFinished:
            self.updateAllImages()

        if self.progressWin is not None:
            self.progressWin.workerFinished = True
            self.progressWin.close()

    @exception_handler
    def trackingWorkerFinished(self):
        if self.progressWin is not None:
            self.progressWin.workerFinished = True
            self.progressWin.close()
        self.logger.info('Worker process ended.')
        askDisableRealTimeTracking = (
            self.trackingWorker.trackingOnNeverVisitedFrames
            and self.realTimeTrackingToggle.isChecked()
        )
        if askDisableRealTimeTracking:
            msg = widgets.myMessageBox()
            title = 'Disable real-time tracking?'
            txt = (
                'You perfomed tracking on frames that you have '
                '<b>never visited.</b><br><br>'
                'Cell-ACDC default behaviour is to <b>track them again</b> when you '
                'will visit them.<br><br>'
                'However, you can <b>overwrite this behaviour</b> and explicitly '
                'disable tracking for all of the frames you already tracked.<br><br>'
                'NOTE: you can reactivate real-time tracking by clicking on the '
                '"Reset last segmented frame" button on the top toolbar.<br><br>'
                'What do you want me to do?'
            )
            _, disableTrackingButton = msg.information(
                self, title, html_utils.paragraph(txt),
                buttonsTexts=(
                    'Keep real-time tracking active (recommended)',
                    'Disable real-time tracking'
                )
            )
            if msg.clickedButton == disableTrackingButton:
                self.realTimeTrackingToggle.setChecked(False)
                posData = self.data[self.pos_i]
                current_frame_i = posData.frame_i
                for frame_i in range(self.start_n-1, self.stop_n):
                    posData.frame_i = frame_i
                    self.get_data()
                    self.store_data(autosave=frame_i==self.stop_n-1)
                posData.last_tracked_i = frame_i
                self.setNavigateScrollBarMaximum()

                # Back to current frame
                posData.frame_i = current_frame_i
                self.get_data()
        posData = self.data[self.pos_i]
        self.updateAllImages()
        self.titleLabel.setText('Done', color='w')

    def workerInitProgressbar(self, totalIter):
        self.progressWin.mainPbar.setValue(0)
        if totalIter == 1:
            totalIter = 0
        self.progressWin.mainPbar.setMaximum(totalIter)

    def workerUpdateProgressbar(self, step):
        self.progressWin.mainPbar.update(step)

    def startTrackingWorker(self, posData, video_to_track):
        self.thread = QThread()
        self.trackingWorker = workers.trackingWorker(
            posData, self, video_to_track
        )
        self.trackingWorker.moveToThread(self.thread)
        self.trackingWorker.finished.connect(self.thread.quit)
        self.trackingWorker.finished.connect(self.trackingWorker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        # Custom signals
        self.trackingWorker.signals = workers.signals()
        self.trackingWorker.signals.progress = self.trackingWorker.progress
        self.trackingWorker.signals.progressBar.connect(
            self.workerUpdateProgressbar
        )
        self.trackingWorker.progress.connect(self.workerProgress)
        self.trackingWorker.critical.connect(self.workerCritical)
        self.trackingWorker.finished.connect(self.trackingWorkerFinished)

        self.trackingWorker.debug.connect(self.workerDebug)

        self.thread.started.connect(self.trackingWorker.run)
        self.thread.start()

    def startRelabellingWorker(self, posData):
        self.thread = QThread()
        self.worker = relabelSequentialWorker(posData, self)
        self.worker.moveToThread(self.thread)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.worker.progress.connect(self.workerProgress)
        self.worker.critical.connect(self.workerCritical)
        self.worker.finished.connect(self.workerFinished)
        self.worker.finished.connect(self.relabelWorkerFinished)

        self.worker.debug.connect(self.workerDebug)

        self.thread.started.connect(self.worker.run)
        self.thread.start()
    
    def startPostProcessSegmWorker(self, postProcessKwargs):
        self.thread = QThread()
        self.postProcessWorker = workers.PostProcessSegm(postProcessKwargs, self)
        
        self.postProcessWorker.moveToThread(self.thread)
        self.postProcessWorker.signals.finished.connect(self.thread.quit)
        self.postProcessWorker.signals.finished.connect(
            self.postProcessWorker.deleteLater
        )
        self.thread.finished.connect(self.thread.deleteLater)

        self.postProcessWorker.signals.finished.connect(
            self.postProcessSegmWorkerFinished
        )
        self.postProcessWorker.signals.progress.connect(self.workerProgress)
        self.postProcessWorker.signals.initProgressBar.connect(
            self.workerInitProgressbar
        )
        self.postProcessWorker.signals.progressBar.connect(
            self.workerUpdateProgressbar
        )
        self.postProcessWorker.signals.critical.connect(
            self.workerCritical
        )

        self.thread.started.connect(self.postProcessWorker.run)
        self.thread.start()
    
    def relabelWorkerFinished(self):
        self.updateAllImages()

    def workerDebug(self, item):
        print(f'Updating frame {item.frame_i}')
        print(item.cca_df)
        stored_lab = item.allData_li[item.frame_i]['labels']
        apps.imshow_tk(item.lab, additional_imgs=[stored_lab])
        self.worker.waitCond.wakeAll()

    def keepToolActiveActionToggled(self, checked):
        parentToolButton = self.sender().parentWidget()
        toolName = re.findall('Toggle "(.*)"', parentToolButton.toolTip())[0]
        self.df_settings.at[toolName, 'value'] = 'keepActive'
        self.df_settings.to_csv(self.settings_csv_path)

    def determineSlideshowWinPos(self):
        screens = self.app.screens()
        self.numScreens = len(screens)
        winScreen = self.screen()

        # Center main window and determine location of slideshow window
        # depending on number of screens available
        if self.numScreens > 1:
            for screen in screens:
                if screen != winScreen:
                    winScreen = screen
                    break

        winScreenGeom = winScreen.geometry()
        winScreenCenter = winScreenGeom.center()
        winScreenCenterX = winScreenCenter.x()
        winScreenCenterY = winScreenCenter.y()
        winScreenLeft = winScreenGeom.left()
        winScreenTop = winScreenGeom.top()
        self.slideshowWinLeft = winScreenCenterX - int(850/2)
        self.slideshowWinTop = winScreenCenterY - int(800/2)

    def nonViewerEditMenuOpened(self):
        mode = str(self.modeComboBox.currentText())
        if mode == 'Viewer':
            self.startBlinkingModeCB()

    def getDistantGray(self, desiredGray, bkgrGray):
        isDesiredSimilarToBkgr = (
            abs(desiredGray-bkgrGray) < 0.3
        )
        if isDesiredSimilarToBkgr:
            return 1-desiredGray
        else:
            return desiredGray

    def RGBtoGray(self, R, G, B):
        # see https://stackoverflow.com/questions/17615963/standard-rgb-to-grayscale-conversion
        C_linear = (0.2126*R + 0.7152*G + 0.0722*B)/255
        if C_linear <= 0.0031309:
            gray = 12.92*C_linear
        else:
            gray = 1.055*(C_linear)**(1/2.4) - 0.055
        return gray

    def ruler_cb(self, checked):
        if checked:
            self.disconnectLeftClickButtons()
            self.uncheckLeftClickButtons(self.sender())
            self.connectLeftClickButtons()
        else:
            self.tempSegmentON = False
            self.ax1_rulerPlotItem.setData([], [])
            self.ax1_rulerAnchorsItem.setData([], [])

    def editImgProperties(self, checked=True):
        posData = self.data[self.pos_i]
        posData.askInputMetadata(
            len(self.data),
            ask_SizeT=True,
            ask_TimeIncrement=True,
            ask_PhysicalSizes=True,
            save=True, singlePos=True,
            askSegm3D=False
        )

    def setHoverToolSymbolData(self, xx, yy, ScatterItems, size=None):
        if not xx:
            self.ax1_lostObjScatterItem.setVisible(True)
            self.ax2_lostObjScatterItem.setVisible(True)

        for item in ScatterItems:
            if size is None:
                item.setData(xx, yy)
            else:
                item.setData(xx, yy, size=size)
    
    def updateLabelRoiCircularSize(self, value):
        self.labelRoiCircItemLeft.setSize(value)
        self.labelRoiCircItemRight.setSize(value)
    
    def updateLabelRoiCircularCursor(self, x, y, checked):
        if not self.labelRoiButton.isChecked():
            return
        if not self.labelRoiIsCircularRadioButton.isChecked():
            return
        if self.labelRoiRunning:
            return

        size = self.labelRoiCircularRadiusSpinbox.value()
        if not checked:
            xx, yy = [], []
        else:
            xx, yy = [x], [y]
        
        if not xx and len(self.labelRoiCircItemLeft.getData()[0]) == 0:
            return

        self.labelRoiCircItemLeft.setData(xx, yy, size=size)
        self.labelRoiCircItemRight.setData(xx, yy, size=size)
    
    def getLabelRoiImage(self):
        posData = self.data[self.pos_i]

        if self.labelRoiTrangeCheckbox.isChecked():
            start_frame_i = self.labelRoiStartFrameNoSpinbox.value()-1
            stop_frame_n = self.labelRoiStopFrameNoSpinbox.value()
            tRangeLen = stop_frame_n-start_frame_i
        else:
            tRangeLen = 1
        
        if tRangeLen > 1:
            tRange = (start_frame_i, stop_frame_n)
        else:
            tRange = None
        
        if self.isSegm3D:
            filteredData = self.filteredData.get(self.user_ch_name)
            if filteredData is None or tRangeLen>1:
                if tRangeLen > 1:
                    imgData = posData.img_data
                else:
                    # Filtered data not existing
                    imgData = posData.img_data[posData.frame_i]
            else:
                # 3D filtered data (see self.applyFilter)
                imgData = filteredData
            
            roi_zdepth = self.labelRoiZdepthSpinbox.value()
            if roi_zdepth == posData.SizeZ:
                z0 = 0
                z1 = posData.SizeZ
            else:
                if roi_zdepth%2 != 0:
                    roi_zdepth +=1
                half_zdepth = int(roi_zdepth/2)
                zc = self.zSliceScrollBar.sliderPosition() + 1
                z0 = zc-half_zdepth
                z0 = z0 if z0>=0 else 0
                z1 = zc+half_zdepth
                z1 = z1 if z1<posData.SizeZ else posData.SizeZ
            if self.labelRoiIsRectRadioButton.isChecked():
                labelRoiSlice = self.labelRoiItem.slice(
                    zRange=(z0,z1), tRange=tRange
                )
            elif self.labelRoiIsFreeHandRadioButton.isChecked():
                labelRoiSlice = self.freeRoiItem.slice(
                    zRange=(z0,z1), tRange=tRange
                )
            elif self.labelRoiIsCircularRadioButton.isChecked():
                labelRoiSlice = self.labelRoiCircItemLeft.slice(
                    zRange=(z0,z1), tRange=tRange
                )
        else:
            if self.labelRoiIsRectRadioButton.isChecked():
                labelRoiSlice = self.labelRoiItem.slice(tRange=tRange)
            elif self.labelRoiIsFreeHandRadioButton.isChecked():
                labelRoiSlice = self.freeRoiItem.slice(tRange=tRange)
            elif self.labelRoiIsCircularRadioButton.isChecked():
                labelRoiSlice = self.labelRoiCircItemLeft.slice(tRange=tRange)
            if tRangeLen > 1:
                imgData = posData.img_data
            else:
                imgData = self.img1.image

        roiImg = imgData[labelRoiSlice]
        if self.labelRoiIsFreeHandRadioButton.isChecked():
            mask = self.freeRoiItem.mask()
        elif self.labelRoiIsCircularRadioButton.isChecked():
            mask = self.labelRoiCircItemLeft.mask()
        else:
            mask = None
        
        if mask is not None:
            # Copy roiImg otherwise we are replacing minimum inside original image
            roiImg = roiImg.copy()
            # Fill outside of freehand roi with minimum of the ROI image
            if tRangeLen > 1:
                for i in range(tRangeLen):
                    ith_roiImg = roiImg[i]
                    if self.isSegm3D:
                        roiImg[i, :, ~mask] = ith_roiImg.min()
                    else:
                        roiImg[i, ~mask] = ith_roiImg.min()
            else:
                if self.isSegm3D:
                    roiImg[:, ~mask] = roiImg.min()
                else:
                    roiImg[~mask] = roiImg.min()

        return roiImg, labelRoiSlice
    
    def getClickedID(self, xdata, ydata, text=''):
        posData = self.data[self.pos_i]
        ID = self.get_2Dlab(posData.lab)[ydata, xdata]
        if ID == 0:
            msg = (
                'You clicked on the background.\n'
                f'Enter here the ID {text}'
            )
            nearest_ID = self.nearest_nonzero(
                self.get_2Dlab(posData.lab), xdata, ydata
            )
            clickedBkgrID = apps.QLineEditDialog(
                title='Clicked on background',
                msg=msg, parent=self, allowedValues=posData.IDs,
                defaultTxt=str(nearest_ID)
            )
            clickedBkgrID.exec_()
            if clickedBkgrID.cancel:
                return
            else:
                ID = clickedBkgrID.EntryID
        return ID

    def getHoverID(self, xdata, ydata):
        if not hasattr(self, 'diskMask'):
            return 0
        
        modifiers = QGuiApplication.keyboardModifiers()
        ctrl = modifiers == Qt.ControlModifier

        if self.isPowerBrush() and not ctrl:
            return 0        

        if not self.editIDcheckbox.isChecked():
            return self.editIDspinbox.value()

        ymin, xmin, ymax, xmax, diskMask = self.getDiskMask(xdata, ydata)
        posData = self.data[self.pos_i]
        lab_2D = self.get_2Dlab(posData.lab)
        ID = lab_2D[ydata, xdata]
        self.isHoverZneighID = False
        if self.isSegm3D:
            z = self.z_lab()
            SizeZ = posData.lab.shape[0]
            doNotLinkThroughZ = (
                self.brushButton.isChecked() and self.isShiftDown
            )
            if doNotLinkThroughZ:
                if self.brushHoverCenterModeAction.isChecked() or ID>0:
                    hoverID = ID
                else:
                    masked_lab = lab_2D[ymin:ymax, xmin:xmax][diskMask]
                    hoverID = np.bincount(masked_lab).argmax()
            else:
                if z > 0:
                    ID_z_under = posData.lab[z-1, ydata, xdata]
                    if self.brushHoverCenterModeAction.isChecked() or ID_z_under>0:
                        hoverIDa = ID_z_under
                    else:
                        lab = posData.lab
                        masked_lab_a = lab[z-1, ymin:ymax, xmin:xmax][diskMask]
                        hoverIDa = np.bincount(masked_lab_a).argmax()
                else:
                    hoverIDa = 0

                if self.brushHoverCenterModeAction.isChecked() or ID>0:
                    hoverIDb = lab_2D[ydata, xdata]
                else:
                    masked_lab_b = lab_2D[ymin:ymax, xmin:xmax][diskMask]
                    hoverIDb = np.bincount(masked_lab_b).argmax()

                if z < SizeZ-1:
                    ID_z_above = posData.lab[z+1, ydata, xdata]
                    if self.brushHoverCenterModeAction.isChecked() or ID_z_above>0:
                        hoverIDc = ID_z_above
                    else:
                        lab = posData.lab
                        masked_lab_c = lab[z+1, ymin:ymax, xmin:xmax][diskMask]
                        hoverIDc = np.bincount(masked_lab_c).argmax()
                else:
                    hoverIDc = 0

                if hoverIDa > 0:
                    hoverID = hoverIDa
                    self.isHoverZneighID = True
                elif hoverIDb > 0:
                    hoverID = hoverIDb
                elif hoverIDc > 0:
                    hoverID = hoverIDc
                    self.isHoverZneighID = True
                else:
                    hoverID = 0
                
                # printl(
                #     f'{doNotLinkThroughZ = },', 
                #     f'{ID_z_under = },'
                #     f'{hoverIDa = }',
                #     f'{hoverIDb = }',
                #     f'{hoverIDc = }',
                #     f'{hoverID = }',
                #     f'{self.brushHoverCenterModeAction = }'
                # )
        else:
            if self.brushButton.isChecked() and self.isShiftDown:
                # Force new ID with brush and Shift
                hoverID = 0
            elif self.brushHoverCenterModeAction.isChecked() or ID>0:
                hoverID = ID
            else:
                masked_lab = lab_2D[ymin:ymax, xmin:xmax][diskMask]
                hoverID = np.bincount(masked_lab).argmax()
            
        self.editIDspinbox.setValue(hoverID)

        return hoverID

    def setHoverToolSymbolColor(
            self, xdata, ydata, pen, ScatterItems, button,
            brush=None, hoverRGB=None, ID=None
        ):

        posData = self.data[self.pos_i]
        Y, X = self.get_2Dlab(posData.lab).shape
        if not myutils.is_in_bounds(xdata, ydata, X, Y):
            return

        self.isHoverZneighID = False
        if ID is None:
            hoverID = self.getHoverID(xdata, ydata)
        else:
            hoverID = ID

        if hoverID == 0:
            for item in ScatterItems:
                item.setPen(pen)
                item.setBrush(brush)
        else:
            try:
                rgb = self.lut[hoverID]
                rgb = rgb if hoverRGB is None else hoverRGB
                rgbPen = np.clip(rgb*1.1, 0, 255)
                for item in ScatterItems:
                    item.setPen(*rgbPen, width=2)
                    item.setBrush(*rgb, 100)
            except IndexError:
                pass
        
        checkChangeID = (
            self.isHoverZneighID and not self.isShiftDown
            and self.lastHoverID != hoverID
        )
        if checkChangeID:
            # We are hovering an ID in z+1 or z-1
            self.restoreBrushID = hoverID
            # self.changeBrushID()
        
        self.lastHoverID = hoverID
    
    def isPowerBrush(self):
        color = self.brushButton.palette().button().color().name()
        return color == self.doublePressKeyButtonColor
    
    def isPowerEraser(self):
        color = self.eraserButton.palette().button().color().name()
        return color == self.doublePressKeyButtonColor
    
    def isPowerButton(self, button):
        color = button.palette().button().color().name()
        return color == self.doublePressKeyButtonColor

    def getCheckNormAction(self):
        normalize = False
        how = ''
        for action in self.normalizeQActionGroup.actions():
            if action.isChecked():
                how = action.text()
                normalize = True
                break
        return action, normalize, how

    def normalizeIntensities(self, img):
        action, normalize, how = self.getCheckNormAction()
        if not normalize:
            return img
        if how == 'Do not normalize. Display raw image':
            img = img 
        elif how == 'Convert to floating point format with values [0, 1]':
            img = myutils.img_to_float(img)
        # elif how == 'Rescale to 8-bit unsigned integer format with values [0, 255]':
        #     img = skimage.img_as_float(img)
        #     img = (img*255).astype(np.uint8)
        #     return img
        elif how == 'Rescale to [0, 1]':
            img = skimage.img_as_float(img)
            img = skimage.exposure.rescale_intensity(img)
        elif how == 'Normalize by max value':
            img = img/np.max(img)
        return img

    def removeAlldelROIsCurrentFrame(self):
        posData = self.data[self.pos_i]
        delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
        rois = delROIs_info['rois'].copy()
        for roi in rois:
            self.ax2.removeItem(roi)

        for item in self.ax2.items:
            if isinstance(item, pg.ROI):
                self.ax2.removeItem(item)
        
        for item in self.ax1.items:
            if isinstance(item, pg.ROI) and item != self.labelRoiItem:
                self.ax1.removeItem(item)

    def removeDelROI(self, event):
        posData = self.data[self.pos_i]

        if isinstance(self.roi_to_del, pg.PolyLineROI):
            self.roi_to_del.clearPoints()
        else:
            self.roi_to_del.setPos((0,0))
            self.roi_to_del.setSize((0,0))
        
        delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
        idx = delROIs_info['rois'].index(self.roi_to_del)
        delROIs_info['rois'].pop(idx)
        delROIs_info['delMasks'].pop(idx)
        delROIs_info['delIDsROI'].pop(idx)
        
        # Restore deleted IDs from already visited future frames
        current_frame_i = posData.frame_i    
        for i in range(posData.frame_i+1, posData.SizeT):
            delROIs_info = posData.allData_li[i]['delROIs_info']
            if self.roi_to_del in delROIs_info['rois']:
                posData.frame_i = i
                idx = delROIs_info['rois'].index(self.roi_to_del)         
                if posData.allData_li[i]['labels'] is not None:
                    if delROIs_info['delIDsROI'][idx]:
                        posData.lab = posData.allData_li[i]['labels']
                        self.restoreAnnotDelROI(self.roi_to_del, enforce=True)
                        posData.allData_li[i]['labels'] = posData.lab
                        self.get_data()
                        self.store_data(autosave=False)
                delROIs_info['rois'].pop(idx)
                delROIs_info['delMasks'].pop(idx)
                delROIs_info['delIDsROI'].pop(idx)
        self.enqAutosave()
        
        if isinstance(self.roi_to_del, pg.PolyLineROI):
            # PolyLine ROIs are only on ax1
            self.ax1.removeItem(self.roi_to_del)
        elif not self.labelsGrad.showLabelsImgAction.isChecked():
            # Rect ROI is on ax1 because ax2 is hidden
            self.ax1.removeItem(self.roi_to_del)
        else:
            # Rect ROI is on ax2 because ax2 is visible
            self.ax2.removeItem(self.roi_to_del)

        # Back to current frame
        posData.frame_i = current_frame_i
        posData.lab = posData.allData_li[posData.frame_i]['labels']                   
        self.get_data()
        self.store_data()

    # @exec_time
    def getPolygonBrush(self, yxc2, Y, X):
        # see https://en.wikipedia.org/wiki/Tangent_lines_to_circles
        y1, x1 = self.yPressAx2, self.xPressAx2
        y2, x2 = yxc2
        R = self.brushSizeSpinbox.value()
        r = R

        arcsin_den = np.sqrt((x2-x1)**2+(y2-y1)**2)
        arctan_den = (x2-x1)
        if arcsin_den!=0 and arctan_den!=0:
            beta = np.arcsin((R-r)/arcsin_den)
            gamma = -np.arctan((y2-y1)/arctan_den)
            alpha = gamma-beta
            x3 = x1 + r*np.sin(alpha)
            y3 = y1 + r*np.cos(alpha)
            x4 = x2 + R*np.sin(alpha)
            y4 = y2 + R*np.cos(alpha)

            alpha = gamma+beta
            x5 = x1 - r*np.sin(alpha)
            y5 = y1 - r*np.cos(alpha)
            x6 = x2 - R*np.sin(alpha)
            y6 = y2 - R*np.cos(alpha)

            rr_poly, cc_poly = skimage.draw.polygon([y3, y4, y6, y5],
                                                    [x3, x4, x6, x5],
                                                    shape=(Y, X))
        else:
            rr_poly, cc_poly = [], []

        self.yPressAx2, self.xPressAx2 = y2, x2
        return rr_poly, cc_poly

    def get_dir_coords(self, alfa_dir, yd, xd, shape, connectivity=1):
        h, w = shape
        y_above = yd+1 if yd+1 < h else yd
        y_below = yd-1 if yd > 0 else yd
        x_right = xd+1 if xd+1 < w else xd
        x_left = xd-1 if xd > 0 else xd
        if alfa_dir == 0:
            yy = [y_below, y_below, yd, y_above, y_above]
            xx = [xd, x_right, x_right, x_right, xd]
        elif alfa_dir == 45:
            yy = [y_below, y_below, y_below, yd, y_above]
            xx = [x_left, xd, x_right, x_right, x_right]
        elif alfa_dir == 90:
            yy = [yd, y_below, y_below, y_below, yd]
            xx = [x_left, x_left, xd, x_right, x_right]
        elif alfa_dir == 135:
            yy = [y_above, yd, y_below, y_below, y_below]
            xx = [x_left, x_left, x_left, xd, x_right]
        elif alfa_dir == -180 or alfa_dir == 180:
            yy = [y_above, y_above, yd, y_below, y_below]
            xx = [xd, x_left, x_left, x_left, xd]
        elif alfa_dir == -135:
            yy = [y_below, yd, y_above, y_above, y_above]
            xx = [x_left, x_left, x_left, xd, x_right]
        elif alfa_dir == -90:
            yy = [yd, y_above, y_above, y_above, yd]
            xx = [x_left, x_left, xd, x_right, x_right]
        else:
            yy = [y_above, y_above, y_above, yd, y_below]
            xx = [x_left, xd, x_right, x_right, x_right]
        if connectivity == 1:
            return yy[1:4], xx[1:4]
        else:
            return yy, xx

    def drawAutoContour(self, y2, x2):
        y1, x1 = self.autoCont_y0, self.autoCont_x0
        Dy = abs(y2-y1)
        Dx = abs(x2-x1)
        edge = self.getDisplayedImg1()
        if Dy != 0 or Dx != 0:
            # NOTE: numIter takes care of any lag in mouseMoveEvent
            numIter = int(round(max((Dy, Dx))))
            alfa = np.arctan2(y1-y2, x2-x1)
            base = np.pi/4
            alfa_dir = round((base * round(alfa/base))*180/np.pi)
            for _ in range(numIter):
                y1, x1 = self.autoCont_y0, self.autoCont_x0
                yy, xx = self.get_dir_coords(alfa_dir, y1, x1, edge.shape)
                a_dir = edge[yy, xx]
                min_int = np.max(a_dir)
                min_i = list(a_dir).index(min_int)
                y, x = yy[min_i], xx[min_i]
                try:
                    xx, yy = self.curvHoverPlotItem.getData()
                except TypeError:
                    xx, yy = [], []
                if x == xx[-1] and yy == yy[-1]:
                    # Do not append point equal to last point
                    return
                xx = np.r_[xx, x]
                yy = np.r_[yy, y]
                try:
                    self.curvHoverPlotItem.setData(xx, yy)
                    self.curvPlotItem.setData(xx, yy)
                except TypeError:
                    pass
                self.autoCont_y0, self.autoCont_x0 = y, x
                # self.smoothAutoContWithSpline()

    def smoothAutoContWithSpline(self, n=3):
        try:
            xx, yy = self.curvHoverPlotItem.getData()
            # Downsample by taking every nth coord
            xxA, yyA = xx[::n], yy[::n]
            rr, cc = skimage.draw.polygon(yyA, xxA)
            self.autoContObjMask[rr, cc] = 1
            rp = skimage.measure.regionprops(self.autoContObjMask)
            if not rp:
                return
            obj = rp[0]
            cont = self.getObjContours(obj)
            xxC, yyC = cont[:,0], cont[:,1]
            xxA, yyA = xxC[::n], yyC[::n]
            self.xxA_autoCont, self.yyA_autoCont = xxA, yyA
            xxS, yyS = self.getSpline(xxA, yyA, per=True, appendFirst=True)
            if len(xxS)>0:
                self.curvPlotItem.setData(xxS, yyS)
        except TypeError:
            pass

    def updateIsHistoryKnown():
        """
        This function is called every time the user saves and it is used
        for updating the status of cells where we don't know the history

        There are three possibilities:

        1. The cell with unknown history is a BUD
           --> we don't know when that  bud emerged --> 'emerg_frame_i' = -1
        2. The cell with unknown history is a MOTHER cell
           --> we don't know emerging frame --> 'emerg_frame_i' = -1
               AND generation number --> we start from 'generation_num' = 2
        3. The cell with unknown history is a CELL in G1
           --> we don't know emerging frame -->  'emerg_frame_i' = -1
               AND generation number --> we start from 'generation_num' = 2
               AND relative's ID in the previous cell cycle --> 'relative_ID' = -1
        """
        pass

    def getStatusKnownHistoryBud(self, ID):
        posData = self.data[self.pos_i]
        cca_df_ID = None
        for i in range(posData.frame_i-1, -1, -1):
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)
            is_cell_existing = is_bud_existing = ID in cca_df_i.index
            if not is_cell_existing:
                cca_df_ID = pd.Series({
                    'cell_cycle_stage': 'S',
                    'generation_num': 0,
                    'relative_ID': -1,
                    'relationship': 'bud',
                    'emerg_frame_i': i+1,
                    'division_frame_i': -1,
                    'is_history_known': True,
                    'corrected_assignment': False,
                    'will_divide': 0,
                })
                return cca_df_ID

    def setHistoryKnowledge(self, ID, cca_df):
        posData = self.data[self.pos_i]
        is_history_known = cca_df.at[ID, 'is_history_known']
        if is_history_known:
            cca_df.at[ID, 'is_history_known'] = False
            cca_df.at[ID, 'cell_cycle_stage'] = 'G1'
            cca_df.at[ID, 'generation_num'] += 2
            cca_df.at[ID, 'emerg_frame_i'] = -1
            cca_df.at[ID, 'relative_ID'] = -1
            cca_df.at[ID, 'relationship'] = 'mother'
        else:
            cca_df.loc[ID] = posData.ccaStatus_whenEmerged[ID]

    def annotateIsHistoryKnown(self, ID):
        """
        This function is used for annotating that a cell has unknown or known
        history. Cells with unknown history are for example the cells already
        present in the first frame or cells that appear in the frame from
        outside of the field of view.

        With this function we simply set 'is_history_known' to False.
        When the users saves instead we update the entire staus of the cell
        with unknown history with the function "updateIsHistoryKnown()"
        """
        posData = self.data[self.pos_i]
        is_history_known = posData.cca_df.at[ID, 'is_history_known']
        relID = posData.cca_df.at[ID, 'relative_ID']
        if relID in posData.cca_df.index:
            relID_cca = self.getStatus_RelID_BeforeEmergence(ID, relID)

        if is_history_known:
            # Save status of ID when emerged to allow undoing
            statusID_whenEmerged = self.getStatusKnownHistoryBud(ID)
            if statusID_whenEmerged is None:
                return
            posData.ccaStatus_whenEmerged[ID] = statusID_whenEmerged

        # Store cca_df for undo action
        undoId = uuid.uuid4()
        self.storeUndoRedoCca(posData.frame_i, posData.cca_df, undoId)

        if ID not in posData.ccaStatus_whenEmerged:
            self.warnSettingHistoryKnownCellsFirstFrame(ID)
            return

        self.setHistoryKnowledge(ID, posData.cca_df)

        if relID in posData.cca_df.index:
            # If the cell with unknown history has a relative ID assigned to it
            # we set the cca of it to the status it had BEFORE the assignment
            posData.cca_df.loc[relID] = relID_cca

        # Update cell cycle info LabelItems
        obj_idx = posData.IDs.index(ID)
        rp_ID = posData.rp[obj_idx]

        if relID in posData.IDs:
            relObj_idx = posData.IDs.index(relID)
            rp_relID = posData.rp[relObj_idx]
        
        self.setAllTextAnnotations()

        self.store_cca_df()

        if self.ccaTableWin is not None:
            self.ccaTableWin.updateTable(posData.cca_df)

        # Correct future frames
        for i in range(posData.frame_i+1, posData.SizeT):
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)
            if cca_df_i is None:
                # ith frame was not visited yet
                break

            self.storeUndoRedoCca(i, cca_df_i, undoId)
            IDs = cca_df_i.index
            if ID not in IDs:
                # For some reason ID disappeared from this frame
                continue
            else:
                self.setHistoryKnowledge(ID, cca_df_i)
                if relID in IDs:
                    cca_df_i.loc[relID] = relID_cca
                self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)


        # Correct past frames
        for i in range(posData.frame_i-1, -1, -1):
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)
            if cca_df_i is None:
                # ith frame was not visited yet
                break

            self.storeUndoRedoCca(i, cca_df_i, undoId)
            IDs = cca_df_i.index
            if ID not in IDs:
                # we reached frame where ID was not existing yet
                break
            else:
                relID = cca_df_i.at[ID, 'relative_ID']
                self.setHistoryKnowledge(ID, cca_df_i)
                if relID in IDs:
                    cca_df_i.loc[relID] = relID_cca
                self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)
        
        self.enqAutosave()
    
    def storeWillDivide(self, ID, relID):
        posData = self.data[self.pos_i]

        # Store in the past S frames that division has been annotated
        for frame_i in range(posData.frame_i, 0, -1):
            past_cca_df = self.get_cca_df(frame_i=frame_i, return_df=True)
            if past_cca_df is None:
                return
            
            if ID not in past_cca_df.index:
                return
            
            ccs = past_cca_df.at[ID, 'cell_cycle_stage']
            if ccs == 'G1':
                return
            
            past_cca_df.at[ID, 'will_divide'] = 1
            past_cca_df.at[relID, 'will_divide'] = 1

            self.store_cca_df(
                cca_df=past_cca_df, frame_i=frame_i, autosave=False
            )

    def annotateDivision(self, cca_df, ID, relID):
        # Correct as follows:
        # For frame_i > 0 --> assign to G1 and +1 on generation number
        # For frame == 0 --> reinitialize to unknown cells
        posData = self.data[self.pos_i]

        self.storeWillDivide(ID, relID)

        store = False
        cca_df.at[ID, 'cell_cycle_stage'] = 'G1'
        cca_df.at[relID, 'cell_cycle_stage'] = 'G1'
        
        if posData.frame_i > 0:
            gen_num_clickedID = cca_df.at[ID, 'generation_num']
            cca_df.at[ID, 'generation_num'] += 1
            cca_df.at[ID, 'division_frame_i'] = posData.frame_i    
            gen_num_relID = cca_df.at[relID, 'generation_num']
            cca_df.at[relID, 'generation_num'] = gen_num_relID+1
            cca_df.at[relID, 'division_frame_i'] = posData.frame_i
            if gen_num_clickedID < gen_num_relID:
                cca_df.at[ID, 'relationship'] = 'mother'
            else:
                cca_df.at[relID, 'relationship'] = 'mother'
        else:
            cca_df.at[ID, 'generation_num'] = 2
            cca_df.at[relID, 'generation_num'] = 2

            cca_df.at[ID, 'division_frame_i'] = -1
            cca_df.at[relID, 'division_frame_i'] = -1

            cca_df.at[ID, 'relationship'] = 'mother' 
            cca_df.at[relID, 'relationship'] = 'mother'
        
        store = True
        return store

    def undoDivisionAnnotation(self, cca_df, ID, relID):
        # Correct as follows:
        # If G1 then correct to S and -1 on generation number
        store = False
        cca_df.at[ID, 'cell_cycle_stage'] = 'S'
        gen_num_clickedID = cca_df.at[ID, 'generation_num']
        cca_df.at[ID, 'generation_num'] -= 1
        cca_df.at[ID, 'division_frame_i'] = -1
        cca_df.at[relID, 'cell_cycle_stage'] = 'S'
        gen_num_relID = cca_df.at[relID, 'generation_num']
        cca_df.at[relID, 'generation_num'] -= 1
        cca_df.at[relID, 'division_frame_i'] = -1
        if gen_num_clickedID < gen_num_relID:
            cca_df.at[ID, 'relationship'] = 'bud'
        else:
            cca_df.at[relID, 'relationship'] = 'bud'
        cca_df.at[ID, 'will_divide'] = 0
        cca_df.at[relID, 'will_divide'] = 0
        store = True
        return store

    def undoBudMothAssignment(self, ID):
        posData = self.data[self.pos_i]
        relID = posData.cca_df.at[ID, 'relative_ID']
        ccs = posData.cca_df.at[ID, 'cell_cycle_stage']
        if ccs == 'G1':
            return
        posData.cca_df.at[ID, 'relative_ID'] = -1
        posData.cca_df.at[ID, 'generation_num'] = 2
        posData.cca_df.at[ID, 'cell_cycle_stage'] = 'G1'
        posData.cca_df.at[ID, 'relationship'] = 'mother'
        if relID in posData.cca_df.index:
            posData.cca_df.at[relID, 'relative_ID'] = -1
            posData.cca_df.at[relID, 'generation_num'] = 2
            posData.cca_df.at[relID, 'cell_cycle_stage'] = 'G1'
            posData.cca_df.at[relID, 'relationship'] = 'mother'

        obj_idx = posData.IDs.index(ID)
        relObj_idx = posData.IDs.index(relID)
        rp_ID = posData.rp[obj_idx]
        rp_relID = posData.rp[relObj_idx]

        self.store_cca_df()

        # Update cell cycle info LabelItems
        self.setAllTextAnnotations()


        if self.ccaTableWin is not None:
            self.ccaTableWin.updateTable(posData.cca_df)

    @exception_handler
    def manualCellCycleAnnotation(self, ID):
        """
        This function is used for both annotating division or undoing the
        annotation. It can be called on any frame.

        If we annotate division (right click on a cell in S) then it will
        check if there are future frames to correct.
        Frames to correct are those frames where both the mother and the bud
        are annotated as S phase cells.
        In this case we assign all those frames to G1, relationship to mother,
        and +1 generation number

        If we undo the annotation (right click on a cell in G1) then it will
        correct both past and future annotated frames (if present).
        Frames to correct are those frames where both the mother and the bud
        are annotated as G1 phase cells.
        In this case we assign all those frames to G1, relationship back to
        bud, and -1 generation number
        """
        posData = self.data[self.pos_i]

        # Store cca_df for undo action
        undoId = uuid.uuid4()
        self.storeUndoRedoCca(posData.frame_i, posData.cca_df, undoId)

        # Correct current frame
        clicked_ccs = posData.cca_df.at[ID, 'cell_cycle_stage']
        relID = posData.cca_df.at[ID, 'relative_ID']

        if relID not in posData.IDs:
            return
        
        if clicked_ccs == 'G1' and posData.frame_i == 0:
            # We do not allow undoing division annotation on first frame
            return

        ccs_relID = posData.cca_df.at[relID, 'cell_cycle_stage']
        if clicked_ccs == 'S':
            store = self.annotateDivision(posData.cca_df, ID, relID)
            self.store_cca_df()
        else:
            store = self.undoDivisionAnnotation(posData.cca_df, ID, relID)
            self.store_cca_df()

        obj_idx = posData.IDs.index(ID)
        relObj_idx = posData.IDs.index(relID)
        rp_ID = posData.rp[obj_idx]
        rp_relID = posData.rp[relObj_idx]

        # Update cell cycle info LabelItems
        self.ax1_newMothBudLinesItem.setData([], [])
        self.ax1_oldMothBudLinesItem.setData([], [])
        self.ax2_newMothBudLinesItem.setData([], [])
        self.ax2_oldMothBudLinesItem.setData([], [])
        self.drawAllMothBudLines()
        self.setAllTextAnnotations()

        if self.ccaTableWin is not None:
            self.ccaTableWin.updateTable(posData.cca_df)

        # Correct future frames
        for i in range(posData.frame_i+1, posData.SizeT):
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)
            if cca_df_i is None:
                # ith frame was not visited yet
                break

            self.storeUndoRedoCca(i, cca_df_i, undoId)
            IDs = cca_df_i.index
            if ID not in IDs:
                # For some reason ID disappeared from this frame
                continue
            else:
                ccs = cca_df_i.at[ID, 'cell_cycle_stage']
                relID = cca_df_i.at[ID, 'relative_ID']
                ccs_relID = cca_df_i.at[relID, 'cell_cycle_stage']
                if clicked_ccs == 'S':
                    if ccs == 'G1':
                        # Cell is in G1 in the future again so stop annotating
                        break
                    self.annotateDivision(cca_df_i, ID, relID)
                    self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)
                else:
                    if ccs == 'S':
                        # Cell is in S in the future again so stop undoing (break)
                        # also leave a 1 frame duration G1 to avoid a continuous
                        # S phase
                        self.annotateDivision(cca_df_i, ID, relID)
                        self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)
                        break
                    store = self.undoDivisionAnnotation(cca_df_i, ID, relID)
                    self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)

        # Correct past frames
        for i in range(posData.frame_i-1, -1, -1):
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)
            if ID not in cca_df_i.index or relID not in cca_df_i.index:
                # Bud did not exist at frame_i = i
                break

            self.storeUndoRedoCca(i, cca_df_i, undoId)
            ccs = cca_df_i.at[ID, 'cell_cycle_stage']
            relID = cca_df_i.at[ID, 'relative_ID']
            ccs_relID = cca_df_i.at[relID, 'cell_cycle_stage']
            if ccs == 'S':
                # We correct only those frames in which the ID was in 'G1'
                break
            else:
                store = self.undoDivisionAnnotation(cca_df_i, ID, relID)
                self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)
        
        self.enqAutosave()

    def warnMotherNotEligible(self, new_mothID, budID, i, why):
        if why == 'not_G1_in_the_future':
            err_msg = html_utils.paragraph(f"""
                The requested cell in G1 (ID={new_mothID})
                at future frame {i+1} has a bud assigned to it,
                therefore it cannot be assigned as the mother
                of bud ID {budID}.<br>
                You can assign a cell as the mother of bud ID {budID}
                only if this cell is in G1 for the
                entire life of the bud.<br><br>
                One possible solution is to click on "cancel", go to
                frame {i+1} and  assign the bud of cell {new_mothID}
                to another cell.\n'
                A second solution is to assign bud ID {budID} to cell
                {new_mothID} anyway by clicking "Apply".<br><br>
                However to ensure correctness of
                future assignments the system will delete any cell cycle
                information from frame {i+1} to the end. Therefore, you
                will have to visit those frames again.<br><br>
                The deletion of cell cycle information
                <b>CANNOT BE UNDONE!</b>
                Saved data is not changed of course.<br><br>
                Apply assignment or cancel process?
            """)
            msg = QMessageBox()
            enforce_assignment = msg.warning(
               self, 'Cell not eligible', err_msg, msg.Apply | msg.Cancel
            )
            cancel = enforce_assignment == msg.Cancel
        elif why == 'not_G1_in_the_past':
            err_msg = html_utils.paragraph(f"""
                The requested cell in G1
                (ID={new_mothID}) at past frame {i+1}
                has a bud assigned to it, therefore it cannot be
                assigned as mother of bud ID {budID}.<br>
                You can assign a cell as the mother of bud ID {budID}
                only if this cell is in G1 for the entire life of the bud.<br>
                One possible solution is to first go to frame {i+1} and
                assign the bud of cell {new_mothID} to another cell.
            """)
            msg = QMessageBox()
            msg.warning(
               self, 'Cell not eligible', err_msg, msg.Ok
            )
            cancel = None
        elif why == 'single_frame_G1_duration':
            err_msg = html_utils.paragraph(f"""
                Assigning bud ID {budID} to cell in G1
                (ID={new_mothID}) would result in no G1 phase at all between
                previous cell cycle and current cell cycle.
                This is very confusing for me, sorry.<br><br>
                The solution is to remove cell division anotation on cell
                {new_mothID} (right-click on it on current frame) and then
                annotate division on any frame before current frame number {i+1}.
                This will gurantee a G1 duration of cell {new_mothID}
                of <b>at least 1 frame</b>. Thanks.
            """)
            msg = widgets.myMessageBox()
            msg.warning(
               self, 'Cell not eligible', err_msg
            )
            cancel = None
        return cancel
    
    def warnSettingHistoryKnownCellsFirstFrame(self, ID):
        txt = html_utils.paragraph(f"""
            Cell ID {ID} is a cell that is <b>present since the first 
            frame.</b><br><br>
            These cells already have history UNKNOWN assigned and the 
            history status <b>cannot be changed.</b>
        """)
        msg = widgets.myMessageBox(wrapText=False)
        msg.warning(
            self, 'First frame cells', txt
        )

    def checkMothEligibility(self, budID, new_mothID):
        """
        Check that the new mother is in G1 for the entire life of the bud
        and that the G1 duration is > than 1 frame
        """
        posData = self.data[self.pos_i]
        eligible = True

        G1_duration = 0
        # Check future frames
        for i in range(posData.frame_i, posData.SizeT):
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)

            if cca_df_i is None:
                # ith frame was not visited yet
                break
            
            if budID not in cca_df_i.index:
                # Bud disappeared
                break

            is_still_bud = cca_df_i.at[budID, 'relationship'] == 'bud'
            if not is_still_bud:
                break

            ccs = cca_df_i.at[new_mothID, 'cell_cycle_stage']
            if ccs != 'G1':
                cancel = self.warnMotherNotEligible(
                    new_mothID, budID, i, 'not_G1_in_the_future'
                )
                if cancel or G1_duration == 1:
                    eligible = False
                    return eligible
                else:
                    self.remove_future_cca_df(i)
                    break

            G1_duration += 1

        # Check past frames
        for i in range(posData.frame_i-1, -1, -1):
            # Get cca_df for ith frame from allData_li
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)

            is_bud_existing = budID in cca_df_i.index
            is_moth_existing = new_mothID in cca_df_i.index

            if not is_moth_existing:
                # Mother not existing because it appeared from outside FOV
                break

            ccs = cca_df_i.at[new_mothID, 'cell_cycle_stage']
            if ccs != 'G1' and is_bud_existing:
                # Requested mother not in G1 in the past
                # during the life of the bud (is_bud_existing = True)
                self.warnMotherNotEligible(
                    new_mothID, budID, i, 'not_G1_in_the_past'
                )
                eligible = False
                return eligible

            if ccs != 'G1':
                # Stop counting G1 duration of the requested mother
                break

            G1_duration += 1

        if G1_duration == 1:
            # G1_duration of the mother is single frame --> not eligible
            eligible = False
            self.warnMotherNotEligible(
                new_mothID, budID, posData.frame_i, 'single_frame_G1_duration'
            )
        return eligible

    def getStatus_RelID_BeforeEmergence(self, budID, curr_mothID):
        posData = self.data[self.pos_i]
        # Get status of the current mother before it had budID assigned to it
        for i in range(posData.frame_i-1, -1, -1):
            # Get cca_df for ith frame from allData_li
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)

            is_bud_existing = budID in cca_df_i.index
            if not is_bud_existing:
                # Bud was not emerged yet
                if curr_mothID in cca_df_i.index:
                    return cca_df_i.loc[curr_mothID]
                else:
                    # The bud emerged together with the mother because
                    # they appeared together from outside of the fov
                    # and they were trated as new IDs bud in S0
                    return pd.Series({
                        'cell_cycle_stage': 'S',
                        'generation_num': 0,
                        'relative_ID': -1,
                        'relationship': 'bud',
                        'emerg_frame_i': i+1,
                        'division_frame_i': -1,
                        'is_history_known': True,
                        'corrected_assignment': False,
                        'will_divide': 0
                    })

    def assignBudMoth(self):
        """
        This function is used for correcting automatic mother-bud assignment.

        It can be called at any frame of the bud life.

        There are three cells involved: bud, current mother, new mother.

        Eligibility:
            - User clicked first on a bud (checked at click time)
            - User released mouse button on a cell in G1 (checked at release time)
            - The new mother MUST be in G1 for all the frames of the bud life
              --> if not warn

        Result:
            - The bud only changes relative ID to the new mother
            - The new mother changes relative ID and stage to 'S'
            - The old mother changes its entire status to the status it had
              before being assigned to the clicked bud
        """
        posData = self.data[self.pos_i]
        budID = self.get_2Dlab(posData.lab)[self.yClickBud, self.xClickBud]
        new_mothID = self.get_2Dlab(posData.lab)[self.yClickMoth, self.xClickMoth]

        if budID == new_mothID:
            return

        # Allow partial initialization of cca_df with mouse
        singleFrameCca = (
            (posData.frame_i == 0 and budID != new_mothID)
            or (self.isSnapshot and budID != new_mothID)
        )
        if singleFrameCca:
            newMothCcs = posData.cca_df.at[new_mothID, 'cell_cycle_stage']
            if not newMothCcs == 'G1':
                err_msg = (
                    'You are assigning the bud to a cell that is not in G1!'
                )
                msg = QMessageBox()
                msg.critical(
                   self, 'New mother not in G1!', err_msg, msg.Ok
                )
                return
            # Store cca_df for undo action
            undoId = uuid.uuid4()
            self.storeUndoRedoCca(0, posData.cca_df, undoId)
            currentRelID = posData.cca_df.at[budID, 'relative_ID']
            if currentRelID in posData.cca_df.index:
                posData.cca_df.at[currentRelID, 'relative_ID'] = -1
                posData.cca_df.at[currentRelID, 'generation_num'] = 2
                posData.cca_df.at[currentRelID, 'cell_cycle_stage'] = 'G1'
                currentRelObjIdx = posData.IDs.index(currentRelID)
                currentRelObj = posData.rp[currentRelObjIdx]
            posData.cca_df.at[budID, 'relationship'] = 'bud'
            posData.cca_df.at[budID, 'generation_num'] = 0
            posData.cca_df.at[budID, 'relative_ID'] = new_mothID
            posData.cca_df.at[budID, 'cell_cycle_stage'] = 'S'
            posData.cca_df.at[new_mothID, 'relative_ID'] = budID
            posData.cca_df.at[new_mothID, 'generation_num'] = 2
            posData.cca_df.at[new_mothID, 'cell_cycle_stage'] = 'S'
            bud_obj_idx = posData.IDs.index(budID)
            new_moth_obj_idx = posData.IDs.index(new_mothID)
            self.updateAllImages()
            self.store_cca_df()
            return

        curr_mothID = posData.cca_df.at[budID, 'relative_ID']

        eligible = self.checkMothEligibility(budID, new_mothID)
        if not eligible:
            return

        if curr_mothID in posData.cca_df.index:
            curr_moth_cca = self.getStatus_RelID_BeforeEmergence(
                                                         budID, curr_mothID)

        # Store cca_df for undo action
        undoId = uuid.uuid4()
        self.storeUndoRedoCca(posData.frame_i, posData.cca_df, undoId)
        # Correct current frames and update LabelItems
        posData.cca_df.at[budID, 'relative_ID'] = new_mothID
        posData.cca_df.at[budID, 'generation_num'] = 0
        posData.cca_df.at[budID, 'relative_ID'] = new_mothID
        posData.cca_df.at[budID, 'relationship'] = 'bud'
        posData.cca_df.at[budID, 'corrected_assignment'] = True
        posData.cca_df.at[budID, 'cell_cycle_stage'] = 'S'

        posData.cca_df.at[new_mothID, 'relative_ID'] = budID
        posData.cca_df.at[new_mothID, 'cell_cycle_stage'] = 'S'
        posData.cca_df.at[new_mothID, 'relationship'] = 'mother'

        if curr_mothID in posData.cca_df.index:
            # Cells with UNKNOWN history has relative's ID = -1
            # which is not an existing cell
            posData.cca_df.loc[curr_mothID] = curr_moth_cca

        bud_obj_idx = posData.IDs.index(budID)
        new_moth_obj_idx = posData.IDs.index(new_mothID)
        if curr_mothID in posData.cca_df.index:
            curr_moth_obj_idx = posData.IDs.index(curr_mothID)
        rp_budID = posData.rp[bud_obj_idx]
        rp_new_mothID = posData.rp[new_moth_obj_idx]

        if curr_mothID in posData.cca_df.index:
            rp_curr_mothID = posData.rp[curr_moth_obj_idx]

        self.updateAllImages()

        self.checkMultiBudMoth(draw=True)

        self.store_cca_df()

        if self.ccaTableWin is not None:
            self.ccaTableWin.updateTable(posData.cca_df)

        # Correct future frames
        for i in range(posData.frame_i+1, posData.SizeT):
            # Get cca_df for ith frame from allData_li
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)
            if cca_df_i is None:
                # ith frame was not visited yet
                break

            IDs = cca_df_i.index
            if budID not in IDs or new_mothID not in IDs:
                # For some reason ID disappeared from this frame
                continue

            self.storeUndoRedoCca(i, cca_df_i, undoId)
            bud_relationship = cca_df_i.at[budID, 'relationship']
            bud_ccs = cca_df_i.at[budID, 'cell_cycle_stage']

            if bud_relationship == 'mother' and bud_ccs == 'S':
                # The bud at the ith frame budded itself --> stop
                break

            cca_df_i.at[budID, 'relative_ID'] = new_mothID
            cca_df_i.at[budID, 'generation_num'] = 0
            cca_df_i.at[budID, 'relative_ID'] = new_mothID
            cca_df_i.at[budID, 'relationship'] = 'bud'
            cca_df_i.at[budID, 'cell_cycle_stage'] = 'S'

            newMoth_bud_ccs = cca_df_i.at[new_mothID, 'cell_cycle_stage']
            if newMoth_bud_ccs == 'G1':
                # Assign bud to new mother only if the new mother is in G1
                # This can happen if the bud already has a G1 annotated
                cca_df_i.at[new_mothID, 'relative_ID'] = budID
                cca_df_i.at[new_mothID, 'cell_cycle_stage'] = 'S'
                cca_df_i.at[new_mothID, 'relationship'] = 'mother'

            if curr_mothID in cca_df_i.index:
                # Cells with UNKNOWN history has relative's ID = -1
                # which is not an existing cell
                cca_df_i.loc[curr_mothID] = curr_moth_cca

            self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)

        # Correct past frames
        for i in range(posData.frame_i-1, -1, -1):
            # Get cca_df for ith frame from allData_li
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)

            is_bud_existing = budID in cca_df_i.index
            if not is_bud_existing:
                # Bud was not emerged yet
                break

            self.storeUndoRedoCca(i, cca_df_i, undoId)
            cca_df_i.at[budID, 'relative_ID'] = new_mothID
            cca_df_i.at[budID, 'generation_num'] = 0
            cca_df_i.at[budID, 'relative_ID'] = new_mothID
            cca_df_i.at[budID, 'relationship'] = 'bud'
            cca_df_i.at[budID, 'cell_cycle_stage'] = 'S'

            cca_df_i.at[new_mothID, 'relative_ID'] = budID
            cca_df_i.at[new_mothID, 'cell_cycle_stage'] = 'S'
            cca_df_i.at[new_mothID, 'relationship'] = 'mother'

            if curr_mothID in cca_df_i.index:
                # Cells with UNKNOWN history has relative's ID = -1
                # which is not an existing cell
                cca_df_i.loc[curr_mothID] = curr_moth_cca

            self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)
        
        self.enqAutosave()
    
    def getClosedSplineCoords(self):
        xxS, yyS = self.curvPlotItem.getData()
        bbox_area = (xxS.max()-xxS.min())*(yyS.max()-yyS.min())
        if bbox_area < 26_000:
            # Using 1000 is fast enough according to profiling
            return xxS, yyS 
        
        optimalSpaceSize = self.splineToObjModel.predict(
            bbox_area, max_exec_time=150
        )
        if optimalSpaceSize >= 1000:
            # Using 1000 is fast enough according to model
            return xxS, yyS
        
        if optimalSpaceSize < 100:
            # Do not allow a rough spline
            optimalSpaceSize = 100
        
        # Get spline with optimal space size so that exec time 
        # or skimage.draw.polygon is less than 150 ms
        xx, yy = self.curvAnchors.getData()
        resolutionSpace = np.linspace(0, 1, int(optimalSpaceSize))
        xxS, yyS = self.getSpline(
            xx, yy, resolutionSpace=resolutionSpace, per=True
        )
        return xxS, yyS


    def getSpline(self, xx, yy, resolutionSpace=None, per=False, appendFirst=False):
        # Remove duplicates
        valid = np.where(np.abs(np.diff(xx)) + np.abs(np.diff(yy)) > 0)
        xx = np.r_[xx[valid], xx[-1]]
        yy = np.r_[yy[valid], yy[-1]]
        if appendFirst:
            xx = np.r_[xx, xx[0]]
            yy = np.r_[yy, yy[0]]
            per = True

        # Interpolate splice
        if resolutionSpace is None:
            resolutionSpace = self.hoverLinSpace
        k = 2 if len(xx) == 3 else 3

        try:
            tck, u = scipy.interpolate.splprep(
                [xx, yy], s=0, k=k, per=per
            )
            xi, yi = scipy.interpolate.splev(resolutionSpace, tck)
            return xi, yi
        except (ValueError, TypeError):
            # Catch errors where we know why splprep fails
            return [], []

    def uncheckQButton(self, button):
        # Manual exclusive where we allow to uncheck all buttons
        for b in self.checkableQButtonsGroup.buttons():
            if b != button:
                b.setChecked(False)

    def delBorderObj(self, checked):
        # Store undo state before modifying stuff
        self.storeUndoRedoStates(False)

        posData = self.data[self.pos_i]
        posData.lab = skimage.segmentation.clear_border(
            posData.lab, buffer_size=1
        )
        oldIDs = posData.IDs.copy()
        self.update_rp()
        removedIDs = [ID for ID in oldIDs if ID not in posData.IDs]
        if posData.cca_df is not None:
            posData.cca_df = posData.cca_df.drop(index=removedIDs)
        self.store_data()
        self.updateAllImages()
    
    def brushAutoFillToggled(self, checked):
        val = 'Yes' if checked else 'No'
        self.df_settings.at['brushAutoFill', 'value'] = val
        self.df_settings.to_csv(self.settings_csv_path)
    
    def brushAutoHideToggled(self, checked):
        val = 'Yes' if checked else 'No'
        self.df_settings.at['brushAutoHide', 'value'] = val
        self.df_settings.to_csv(self.settings_csv_path)

    def addDelROI(self, event):       
        roi = self.getDelROI()
        self.addRoiToDelRoiInfo(roi)
        if not self.labelsGrad.showLabelsImgAction.isChecked():
            self.ax1.addItem(roi)
        else:
            self.ax2.addItem(roi)
        self.applyDelROIimg1(roi, init=True)
        self.applyDelROIimg1(roi, init=True, ax=1)

        if self.isSnapshot:
            self.fixCcaDfAfterEdit('Delete IDs using ROI')
            self.updateAllImages()
        else:
            cancelled_str = self.warnEditingWithCca_df(
                'Delete IDs using ROI', get_cancelled=True
            )
            if cancelled_str == 'cancelled':
                self.roi_to_del = roi
                self.removeDelROI(None)
    
    def replacePolyLineRoiWithLineRoi(self, roi):
        roi = self.polyLineRoi
        x0, y0 = roi.pos().x(), roi.pos().y()
        (_, point1), (_, point2) = roi.getLocalHandlePositions()
        xr1, yr1 = point1.x(), point1.y()
        xr2, yr2 = point2.x(), point2.y()
        x1, y1 = xr1+x0, yr1+y0
        x2, y2 = xr2+x0, yr2+x0
        lineRoi = pg.LineROI((x1, y1), (x2, y2), width=0.5)
        lineRoi.handleSize = 7
        self.ax1.removeItem(self.polyLineRoi)
        self.ax1.addItem(lineRoi)
        lineRoi.removeHandle(2)
        # Connect closed ROI
        lineRoi.sigRegionChanged.connect(self.delROImoving)
        lineRoi.sigRegionChangeFinished.connect(self.delROImovingFinished)
        return lineRoi
    
    def addRoiToDelRoiInfo(self, roi):
        posData = self.data[self.pos_i]
        for i in range(posData.frame_i, posData.SizeT):
            delROIs_info = posData.allData_li[i]['delROIs_info']
            delROIs_info['rois'].append(roi)
            delROIs_info['delMasks'].append(np.zeros_like(posData.lab))
            delROIs_info['delIDsROI'].append(set())
    
    def addDelPolyLineRoi_cb(self, checked):
        if checked:
            self.disconnectLeftClickButtons()
            self.uncheckLeftClickButtons(self.addDelPolyLineRoiAction)
            self.connectLeftClickButtons()
            if self.isSnapshot:
                self.fixCcaDfAfterEdit('Delete IDs using ROI')
                self.updateAllImages()
            else:
                self.warnEditingWithCca_df('Delete IDs using ROI')
        else:
            self.tempSegmentON = False
            self.ax1_rulerPlotItem.setData([], [])
            self.ax1_rulerAnchorsItem.setData([], [])
            self.startPointPolyLineItem.setData([], [])
            while self.app.overrideCursor() is not None:
                self.app.restoreOverrideCursor()
         
    def createDelPolyLineRoi(self):
        Y, X = self.currentLab2D.shape
        self.polyLineRoi = pg.PolyLineROI(
            [], rotatable=False,
            removable=True,
            pen=pg.mkPen(color='r')
        )
        self.polyLineRoi.handleSize = 7
        self.polyLineRoi.points = []
        self.ax1.addItem(self.polyLineRoi)
    
    def addPointsPolyLineRoi(self, closed=False):
        self.polyLineRoi.setPoints(self.polyLineRoi.points, closed=closed)
        if not closed:
            return

        # Connect closed ROI
        self.polyLineRoi.sigRegionChanged.connect(self.delROImoving)
        self.polyLineRoi.sigRegionChangeFinished.connect(self.delROImovingFinished)
    
    def getViewRange(self):
        Y, X = self.img1.image.shape[:2]
        xRange, yRange = self.ax1.viewRange()
        xmin = 0 if xRange[0] < 0 else xRange[0]
        ymin = 0 if yRange[0] < 0 else yRange[0]
        
        xmax = X if xRange[1] >= X else xRange[1]
        ymax = Y if yRange[1] >= Y else yRange[1]
        return int(ymin), int(ymax), int(xmin), int(xmax)

    def getDelROI(self, xl=None, yb=None, w=32, h=32, anchors=None):
        posData = self.data[self.pos_i]
        if xl is None:
            xRange, yRange = self.ax1.viewRange()
            xl = 0 if xRange[0] < 0 else xRange[0]
            yb = 0 if yRange[0] < 0 else yRange[0]
        Y, X = self.currentLab2D.shape
        if anchors is None:
            roi = pg.ROI(
                [xl, yb], [w, h],
                rotatable=False,
                removable=True,
                pen=pg.mkPen(color='r'),
                maxBounds=QRectF(QRect(0,0,X,Y))
            )
            ## handles scaling horizontally around center
            roi.addScaleHandle([1, 0.5], [0, 0.5])
            roi.addScaleHandle([0, 0.5], [1, 0.5])

            ## handles scaling vertically from opposite edge
            roi.addScaleHandle([0.5, 0], [0.5, 1])
            roi.addScaleHandle([0.5, 1], [0.5, 0])

            ## handles scaling both vertically and horizontally
            roi.addScaleHandle([1, 1], [0, 0])
            roi.addScaleHandle([0, 0], [1, 1])
            roi.addScaleHandle([0, 1], [1, 0])
            roi.addScaleHandle([1, 0], [0, 1])

        roi.handleSize = 7
        roi.sigRegionChanged.connect(self.delROImoving)
        roi.sigRegionChanged.connect(self.delROIstartedMoving)
        roi.sigRegionChangeFinished.connect(self.delROImovingFinished)
        return roi
    
    def delROIstartedMoving(self, roi):
        self.ax1_lostObjScatterItem.setData([], [])
        self.ax2_lostObjScatterItem.setData([], [])

    def delROImoving(self, roi):
        roi.setPen(color=(255,255,0))
        # First bring back IDs if the ROI moved away
        self.restoreAnnotDelROI(roi)
        self.setImageImg2()
        self.applyDelROIimg1(roi)
        self.applyDelROIimg1(roi, ax=1)

    def delROImovingFinished(self, roi):
        roi.setPen(color='r')
        self.update_rp()
        self.updateAllImages()

    def restoreAnnotDelROI(self, roi, enforce=True):
        posData = self.data[self.pos_i]
        ROImask = self.getDelRoiMask(roi)
        delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
        idx = delROIs_info['rois'].index(roi)
        delMask = delROIs_info['delMasks'][idx]
        delIDs = delROIs_info['delIDsROI'][idx]
        overlapROIdelIDs = np.unique(delMask[ROImask])
        lab2D = self.get_2Dlab(posData.lab)
        restoredIDs = set()
        for ID in delIDs:
            if ID in overlapROIdelIDs and not enforce:
                continue
            delMaskID = delMask==ID
            self.currentLab2D[delMaskID] = ID
            lab2D[delMaskID] = ID
            self.restoreDelROIimg1(delMaskID, ID, ax=0)
            self.restoreDelROIimg1(delMaskID, ID, ax=1)
            delMask[delMaskID] = 0
            restoredIDs.add(ID)
        delROIs_info['delIDsROI'][idx] = delIDs - restoredIDs
        self.set_2Dlab(lab2D)
        self.update_rp()

    def restoreDelROIimg1(self, delMaskID, delID, ax=0):
        posData = self.data[self.pos_i]
        if ax == 0:
            how = self.drawIDsContComboBox.currentText()
        else:
            how = self.getAnnotateHowRightImage()

        if how.find('nothing') != -1:
            return
        
        obj = skimage.measure.regionprops(delMaskID.astype(np.uint8))[0]
        if how.find('contours') != -1:        
            self.addObjContourToContoursImage(obj=obj, ax=ax)  
        elif how.find('overlay segm. masks') != -1:
            if ax == 0:
                self.labelsLayerImg1.setImage(
                    self.currentLab2D, autoLevels=False
                )
            else:
                self.labelsLayerRightImg.setImage(
                    self.currentLab2D, autoLevels=False
                )

    def getDelRoisIDs(self):
        posData = self.data[self.pos_i]
        if posData.frame_i > 0:
            prev_lab = posData.allData_li[posData.frame_i-1]['labels']
        allDelIDs = set()
        for roi in posData.allData_li[posData.frame_i]['delROIs_info']['rois']:
            if roi not in self.ax2.items and roi not in self.ax1.items:
                continue
            
            ROImask = self.getDelRoiMask(roi)
            delIDs = posData.lab[ROImask]
            allDelIDs.update(delIDs)
            if posData.frame_i > 0:
                delIDsPrevFrame = prev_lab[ROImask]
                allDelIDs.update(delIDsPrevFrame)
        return allDelIDs
    
    def getStoredDelRoiIDs(self, frame_i=None):
        posData = self.data[self.pos_i]
        if frame_i is None:
            frame_i = posData.frame_i
        allDelIDs = set()
        delROIs_info = posData.allData_li[frame_i]['delROIs_info']
        delIDs_rois = delROIs_info['delIDsROI']
        for delIDs in delIDs_rois:
            allDelIDs.update(delIDs)
        return allDelIDs
    
    def getDelROIlab(self):
        posData = self.data[self.pos_i]
        DelROIlab = self.get_2Dlab(posData.lab, force_z=False).copy()
        allDelIDs = set()
        # Iterate rois and delete IDs
        for roi in posData.allData_li[posData.frame_i]['delROIs_info']['rois']:
            if roi not in self.ax2.items and roi not in self.ax1.items:
                continue     
            ROImask = self.getDelRoiMask(roi)
            delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
            idx = delROIs_info['rois'].index(roi)
            delObjROImask = delROIs_info['delMasks'][idx]
            delIDsROI = delROIs_info['delIDsROI'][idx]   
            delIDs = np.unique(posData.lab[ROImask])
            if len(delIDs) > 0:
                if delIDs[0] == 0:
                    delIDs = delIDs[1:]
            delIDsROI.update(delIDs)
            allDelIDs.update(delIDs)
            _DelROIlab = self.get_2Dlab(posData.lab).copy()
            for obj in posData.rp:
                ID = obj.label
                if ID in delIDs:
                    delObjROImask[posData.lab==ID] = ID
                    _DelROIlab[posData.lab==ID] = 0
            DelROIlab[_DelROIlab == 0] = 0
            # Keep a mask of deleted IDs to bring them back when roi moves
            delROIs_info['delMasks'][idx] = delObjROImask
            delROIs_info['delIDsROI'][idx] = delIDsROI
        return allDelIDs, DelROIlab
    
    def getDelRoiMask(self, roi, posData=None, z_slice=None):
        if posData is None:
            posData = self.data[self.pos_i]
        if z_slice is None:
            z_slice = self.z_lab()
        ROImask = np.zeros(posData.lab.shape, bool)
        if isinstance(roi, pg.PolyLineROI):
            r, c = [], []
            x0, y0 = roi.pos().x(), roi.pos().y()
            for _, point in roi.getLocalHandlePositions():
                xr, yr = point.x(), point.y()
                r.append(int(yr+y0))
                c.append(int(xr+x0))
            if not r or not c:
                return ROImask
            
            if len(r) == 2:
                rr, cc, val = skimage.draw.line_aa(r[0], c[0], r[1], c[1])
            else:
                rr, cc = skimage.draw.polygon(r, c, shape=self.currentLab2D.shape)
            if self.isSegm3D:
                ROImask[z_slice, rr, cc] = True
            else:
                ROImask[rr, cc] = True
        elif isinstance(roi, pg.LineROI):
            (_, point1), (_, point2) = roi.getSceneHandlePositions()
            point1 = self.ax1.vb.mapSceneToView(point1)
            point2 = self.ax1.vb.mapSceneToView(point2)
            x1, y1 = int(point1.x()), int(point1.y())
            x2, y2 = int(point2.x()), int(point2.y())
            rr, cc, val = skimage.draw.line_aa(y1, x1, y2, x2)
            if self.isSegm3D:
                ROImask[z_slice, rr, cc] = True
            else:
                ROImask[rr, cc] = True
        else: 
            x0, y0 = [int(c) for c in roi.pos()]
            w, h = [int(c) for c in roi.size()]
            if self.isSegm3D:
                ROImask[z_slice, y0:y0+h, x0:x0+w] = True
            else:
                ROImask[y0:y0+h, x0:x0+w] = True
        return ROImask

    def filterToggled(self, checked):
        action = self.sender()
        filterName = action.filterName
        filterDialogApp = self.filtersWins[filterName]['dialogueApp']
        filterWin = self.filtersWins[filterName]['window']
        if checked:
            posData = self.data[self.pos_i]
            channels = [self.user_ch_name]
            channels.extend(self.checkedOverlayChannels)
            is3D = posData.SizeZ>1
            currentChannel = self.user_ch_name
            filterWin = filterDialogApp(
                channels, parent=self, is3D=is3D, currentChannel=currentChannel
            )
            initMethods = self.filtersWins[filterName].get('initMethods')
            if initMethods is not None:
                localVariables = locals()
                for method_name, args in initMethods.items(): 
                    method = getattr(filterWin, method_name)
                    args = [localVariables[arg] for arg in args]
                    method(*args)
            self.filtersWins[filterName]['window'] = filterWin
            filterWin.action = self.sender()
            filterWin.sigClose.connect(self.filterWinClosed)
            filterWin.sigApplyFilter.connect(self.applyFilter)
            filterWin.sigPreviewToggled.connect(self.previewFilterToggled)
            filterWin.show()
            filterWin.apply()
        elif filterWin is not None:
            filterWin.disconnect()
            filterWin.close()
            self.filteredData = {}
            self.filtersWins[filterName]['window'] = None
            self.updateAllImages()
    
    def filterWinClosed(self, filterWin):
        action = filterWin.action
        filterWin = None
        action.setChecked(False)
    
    def applyFilter(self, channelName, setImg=True):
        posData = self.data[self.pos_i]
        if channelName == self.user_ch_name:
            imgData = posData.img_data[posData.frame_i]
            isLayer0 = True
        else:
            _, filename = self.getPathFromChName(channelName, posData)
            imgData = posData.ol_data_dict[filename][posData.frame_i]
            isLayer0 = False
        filteredData = imgData.copy()
        storeFiltered = False
        for filterDict in self.filtersWins.values():
            filterWin = filterDict['window']
            if filterWin is None:
                continue
            filteredData = filterWin.filter(filteredData)
            storeFiltered = True

        if storeFiltered:
            self.filteredData[channelName] = filteredData

        if posData.SizeZ > 1:
            img = self.get_2Dimg_from_3D(filteredData, isLayer0=isLayer0)
        else:
            img = filteredData
        
        img = self.normalizeIntensities(img)

        if not setImg:
            return img
        
        if channelName == self.user_ch_name:
            self.img1.setImage(img)
        else:
            imageItem = self.overlayLayersItems[channelName][0]
            imageItem.setImage(img)

    def previewFilterToggled(self, checked, filterWin, channelName):
        if checked:
            self.applyGaussBlur(filterWin, channelName)
        else:
            self.updateAllImages()

    def enableSmartTrack(self, checked):
        posData = self.data[self.pos_i]
        # Disable tracking for already visited frames

        if posData.allData_li[posData.frame_i]['labels'] is not None:
            trackingEnabled = True
        else:
            trackingEnabled = False

        if checked:
            self.UserEnforced_DisabledTracking = False
            self.UserEnforced_Tracking = False
        else:
            if trackingEnabled:
                self.UserEnforced_DisabledTracking = True
                self.UserEnforced_Tracking = False
            else:
                self.UserEnforced_DisabledTracking = False
                self.UserEnforced_Tracking = True

    def invertBw(self, checked, update=True):
        self.labelsGrad.invertBwAction.toggled.disconnect()
        self.labelsGrad.invertBwAction.setChecked(checked)
        self.labelsGrad.invertBwAction.toggled.connect(self.setCheckedInvertBW)

        self.imgGrad.invertBwAction.toggled.disconnect()
        self.imgGrad.invertBwAction.setChecked(checked)
        self.imgGrad.invertBwAction.toggled.connect(self.setCheckedInvertBW)

        self.imgGrad.setInvertedColorMaps(checked)
        self.imgGrad.invertCurrentColormap()

        self.imgGradRight.setInvertedColorMaps(checked)
        self.imgGradRight.invertCurrentColormap()

        for items in self.overlayLayersItems.values():
            lutItem = items[1]
            lutItem.invertBwAction.toggled.disconnect()
            lutItem.invertBwAction.setChecked(checked)
            lutItem.invertBwAction.toggled.connect(self.setCheckedInvertBW)
            lutItem.setInvertedColorMaps(checked)

        if self.slideshowWin is not None:
            self.slideshowWin.is_bw_inverted = checked
            self.slideshowWin.update_img()
        self.df_settings.at['is_bw_inverted', 'value'] = 'Yes' if checked else 'No'
        self.df_settings.to_csv(self.settings_csv_path)
        if checked:
            # Light mode
            self.equalizeHistPushButton.setStyleSheet('')
            self.graphLayout.setBackground(graphLayoutBkgrColor)
            self.ax2_BrushCirclePen = pg.mkPen((150,150,150), width=2)
            self.ax2_BrushCircleBrush = pg.mkBrush((200,200,200,150))
            self.titleColor = 'black'    
        else:
            # Dark mode
            self.equalizeHistPushButton.setStyleSheet(
                'QPushButton {background-color: #282828; color: #F0F0F0;}'
            )
            self.graphLayout.setBackground(darkBkgrColor)
            self.ax2_BrushCirclePen = pg.mkPen(width=2)
            self.ax2_BrushCircleBrush = pg.mkBrush((255,255,255,50))
            self.titleColor = 'white'
        
        self.textAnnot[0].invertBlackAndWhite()
        self.textAnnot[1].invertBlackAndWhite()

        self.objLabelAnnotRgb = tuple(
            self.textAnnot[0].item.colors()['label'][:3]
        )
        self.textIDsColorButton.setColor(self.objLabelAnnotRgb)
        
        if update:
            self.updateAllImages()
    
    def _channelHoverValues(self, descr, channel, value, ff=None):
        if ff is None:
            n_digits = len(str(int(value)))
            ff = myutils.get_number_fstring_formatter(
                type(value), precision=abs(n_digits-5)
            )
        txt = (
            f'<b>{descr} {channel}</b>: value={value:{ff}}'
        )
        return txt
    
    def _addOverlayHoverValuesFormatted(self, txt, xdata, ydata):
        posData = self.data[self.pos_i]
        if posData.ol_data is None:
            return txt
        
        for filename in posData.ol_data:
            chName = myutils.get_chname_from_basename(
                filename, posData.basename, remove_ext=False
            )
            if chName not in self.checkedOverlayChannels:
                continue
            
            raw_overlay_img = self.getRawImage(filename=filename)
            raw_overlay_value = raw_overlay_img[ydata, xdata]
            # raw_overlay_max_value = raw_overlay_img.max()

            raw_txt = self._channelHoverValues('Raw', chName, raw_overlay_value)

            txt = f'{txt} | {raw_txt}'
        return txt
    
    def hoverValuesFormatted(self, xdata, ydata):
        activeToolButton = None
        for button in self.LeftClickButtons:
            if button.isChecked():
                activeToolButton = button
                break
        
        txt = f'x={xdata:d}, y={ydata:d}'
        if activeToolButton == self.rulerButton:
            txt = self._addRulerMeasurementText(txt)
            return txt
        elif activeToolButton is not None:
            return txt

        posData = self.data[self.pos_i]

        raw_img = self.getRawImage()
        raw_value = raw_img[ydata, xdata]
        # raw_max_value = raw_img.max()

        ch = self.user_ch_name
        raw_txt = self._channelHoverValues('Raw', ch, raw_value)

        txt = f'{txt} | {raw_txt}'

        txt = self._addOverlayHoverValuesFormatted(txt, xdata, ydata)
        
        ID = self.currentLab2D[ydata, xdata]
        maxID = max(posData.IDs, default=0)

        num_obj = len(posData.IDs)
        lab_txt = (
            f'<b>Objects</b>: ID={ID}, <i>max ID={maxID}, '
            f'num. of objects={num_obj}</i>'
        )
        txt = f'{txt} | {lab_txt}'

        txt = self._addRulerMeasurementText(txt)
        return txt
    
    def _addRulerMeasurementText(self, txt):
        posData = self.data[self.pos_i]
        xx, yy = self.ax1_rulerPlotItem.getData()
        if xx is None:
            return txt

        lenPxl = math.sqrt((xx[0]-xx[1])**2 + (yy[0]-yy[1])**2)
        pxlToUm = posData.PhysicalSizeX
        length_txt = (
            f'length={lenPxl:.2f} pxl ({lenPxl*pxlToUm:.2f} μm)'
        )
        txt = f'{txt} | <b>Measurement</b>: {length_txt}'
        return txt

    def updateImageValueFormatter(self):
        if self.img1.image is not None:
            dtype = self.img1.image.dtype
            n_digits = len(str(int(self.img1.image.max())))
            self.imgValueFormatter = myutils.get_number_fstring_formatter(
                dtype, precision=abs(n_digits-5)
            )

        rawImgData = self.data[self.pos_i].img_data
        dtype = rawImgData.dtype
        n_digits = len(str(int(rawImgData.max())))
        self.rawValueFormatter = myutils.get_number_fstring_formatter(
            dtype, precision=abs(n_digits-5)
        )

    def normaliseIntensitiesActionTriggered(self, action):
        how = action.text()
        self.df_settings.at['how_normIntensities', 'value'] = how
        self.df_settings.to_csv(self.settings_csv_path)
        self.updateAllImages(addFupdateFilters=True)
        self.updateImageValueFormatter()

    def setLastUserNormAction(self):
        how = self.df_settings.at['how_normIntensities', 'value']
        for action in self.normalizeQActionGroup.actions():
            if action.text() == how:
                action.setChecked(True)
                break

    def saveLabelsColormap(self):
        self.labelsGrad.saveColormap()
    
    def addFontSizeActions(self, menu, slot):
        fontActionGroup = QActionGroup(self)
        fontActionGroup.setExclusionPolicy(
            QActionGroup.ExclusionPolicy.Exclusive
        )
        for fontSize in range(4,27):
            action = QAction(self)
            action.setText(str(fontSize))
            action.setCheckable(True)
            if fontSize == self.fontSize:
                action.setChecked(True)
            fontActionGroup.addAction(action)
            menu.addAction(action)
            action.triggered.connect(slot)
        return fontActionGroup

    @exception_handler
    def changeFontSize(self):
        fontSize = self.fontSizeSpinBox.value()
        if fontSize == self.fontSize:
            return
        
        self.fontSize = fontSize

        self.df_settings.at['fontSize', 'value'] = self.fontSize
        self.df_settings.to_csv(self.settings_csv_path)
        
        self.setAllIDs()
        posData = self.data[self.pos_i]
        allIDs = posData.allIDs
        for ax in range(2):
            self.textAnnot[ax].changeFontSize(self.fontSize)
        if self.highLowResToggle.isChecked():
            self.setAllTextAnnotations()
        else:
            self.updateAllImages()

    def enableZstackWidgets(self, enabled):
        if enabled:
            myutils.setRetainSizePolicy(self.zSliceScrollBar)
            myutils.setRetainSizePolicy(self.zProjComboBox)
            myutils.setRetainSizePolicy(self.zSliceOverlay_SB)
            myutils.setRetainSizePolicy(self.zProjOverlay_CB)
            myutils.setRetainSizePolicy(self.overlay_z_label)
            self.zSliceScrollBar.setDisabled(False)
            self.zProjComboBox.show()
            self.zSliceScrollBar.show()
            self.zSliceCheckbox.show()
            self.zSliceSpinbox.show()
            self.SizeZlabel.show()
        else:
            myutils.setRetainSizePolicy(self.zSliceScrollBar, retain=False)
            myutils.setRetainSizePolicy(self.zProjComboBox, retain=False)
            myutils.setRetainSizePolicy(self.zSliceOverlay_SB, retain=False)
            myutils.setRetainSizePolicy(self.zProjOverlay_CB, retain=False)
            myutils.setRetainSizePolicy(self.overlay_z_label, retain=False)
            self.zSliceScrollBar.setDisabled(True)
            self.zProjComboBox.hide()
            self.zSliceScrollBar.hide()
            self.zSliceCheckbox.hide()
            self.zSliceSpinbox.hide()
            self.SizeZlabel.hide()

    def reInitCca(self):
        if not self.isSnapshot:
            txt = html_utils.paragraph(
                'If you decide to continue <b>ALL cell cycle annotations</b> from '
                'this frame to the end will be <b>erased from current session</b> '
                '(saved data is not touched of course).<br><br>'
                'To annotate future frames again you will have to revisit them.<br><br>'
                'Do you want to continue?'
            )
            msg = widgets.myMessageBox()
            msg.warning(
               self, 'Re-initialize annnotations?', txt, 
               buttonsTexts=('Cancel', 'Yes')
            )
            posData = self.data[self.pos_i]
            if msg.cancel:
                return
            # Go to previous frame without storing and then back to current
            if posData.frame_i > 0:
                posData.frame_i -= 1
                self.get_data()
                self.remove_future_cca_df(posData.frame_i)
                self.next_frame()
            else:
                posData.cca_df = self.getBaseCca_df()
                self.remove_future_cca_df(posData.frame_i)
                self.store_data()
                self.updateAllImages()
        else:
            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)

            posData = self.data[self.pos_i]
            posData.cca_df = self.getBaseCca_df()
            self.store_data()
            self.updateAllImages()        


    def repeatAutoCca(self):
        # Do not allow automatic bud assignment if there are future
        # frames that already contain anotations
        posData = self.data[self.pos_i]
        next_df = posData.allData_li[posData.frame_i+1]['acdc_df']
        if next_df is not None:
            if 'cell_cycle_stage' in next_df.columns:
                msg = QMessageBox()
                warn_cca = msg.critical(
                    self, 'Future visited frames detected!',
                    'Automatic bud assignment CANNOT be performed becasue '
                    'there are future frames that already contain cell cycle '
                    'annotations. The behaviour in this case cannot be predicted.\n\n'
                    'We suggest assigning the bud manually OR use the '
                    '"Re-initialize cell cycle annotations" button which properly '
                    're-initialize future frames.',
                    msg.Ok
                )
                return

        correctedAssignIDs = (
                posData.cca_df[posData.cca_df['corrected_assignment']].index)
        NeverCorrectedAssignIDs = [ID for ID in posData.new_IDs
                                   if ID not in correctedAssignIDs]

        # Store cca_df temporarily if attempt_auto_cca fails
        posData.cca_df_beforeRepeat = posData.cca_df.copy()

        if not all(NeverCorrectedAssignIDs):
            notEnoughG1Cells, proceed = self.attempt_auto_cca()
            if notEnoughG1Cells or not proceed:
                posData.cca_df = posData.cca_df_beforeRepeat
            else:
                self.updateAllImages()
            return

        msg = QMessageBox()
        msg.setIcon(msg.Question)
        msg.setText(
            'Do you want to automatically assign buds to mother cells for '
            'ALL the new cells in this frame (excluding cells with unknown history) '
            'OR only the cells where you never clicked on?'
        )
        msg.setDetailedText(
            f'New cells that you never touched:\n\n{NeverCorrectedAssignIDs}')
        enforceAllButton = QPushButton('ALL new cells')
        b = QPushButton('Only cells that I never corrected assignment')
        msg.addButton(b, msg.YesRole)
        msg.addButton(enforceAllButton, msg.NoRole)
        msg.exec_()
        if msg.clickedButton() == enforceAllButton:
            notEnoughG1Cells, proceed = self.attempt_auto_cca(enforceAll=True)
        else:
            notEnoughG1Cells, proceed = self.attempt_auto_cca()
        if notEnoughG1Cells or not proceed:
            posData.cca_df = posData.cca_df_beforeRepeat
        else:
            self.updateAllImages()

    def manualEditCcaToolbarActionTriggered(self):
        self.manualEditCca()
    
    def manualEditCca(self, checked=True):
        posData = self.data[self.pos_i]
        editCcaWidget = apps.editCcaTableWidget(
            posData.cca_df, posData.SizeT, current_frame_i=posData.frame_i,
            parent=self
        )
        editCcaWidget.sigApplyChangesFutureFrames.connect(
            self.applyManualCcaChangesFutureFrames
        )
        editCcaWidget.exec_()
        if editCcaWidget.cancel:
            return
        posData.cca_df = editCcaWidget.cca_df
        self.checkMultiBudMoth()
        self.updateAllImages()
    
    @exception_handler
    def applyManualCcaChangesFutureFrames(self, changes, stop_frame_i):
        self.store_data(autosave=False)
        posData = self.data[self.pos_i]
        undoId = uuid.uuid4()
        for i in range(posData.frame_i, stop_frame_i):
            cca_df_i = self.get_cca_df(frame_i=i, return_df=True)
            if cca_df_i is None:
                # ith frame was not visited yet
                break
            
            self.storeUndoRedoCca(i, cca_df_i, undoId)
            
            for ID, changes_ID in changes.items():
                if ID not in cca_df_i.index:
                    continue
                for col, (oldValue, newValue) in changes_ID.items():
                    cca_df_i.at[ID, col] = newValue
            self.store_cca_df(frame_i=i, cca_df=cca_df_i, autosave=False)
        self.get_data()
        self.updateAllImages()
    
    def annotateRightHowCombobox_cb(self, idx):
        how = self.annotateRightHowCombobox.currentText()
        self.df_settings.at['how_draw_right_annotations', 'value'] = how
        self.df_settings.to_csv(self.settings_csv_path)

        self.textAnnot[1].setCcaAnnot(
            self.annotCcaInfoCheckboxRight.isChecked()
        )
        self.textAnnot[1].setLabelAnnot(
            self.annotIDsCheckboxRight.isChecked()
        )
        if not self.isDataLoading:
            self.updateAllImages()

    def drawIDsContComboBox_cb(self, idx):
        how = self.drawIDsContComboBox.currentText()
        self.df_settings.at['how_draw_annotations', 'value'] = how
        self.df_settings.to_csv(self.settings_csv_path)

        self.textAnnot[0].setCcaAnnot(self.annotCcaInfoCheckbox.isChecked())
        self.textAnnot[0].setLabelAnnot(self.annotIDsCheckbox.isChecked())

        if not self.isDataLoading:
            self.updateAllImages()

        if self.eraserButton.isChecked():
            self.setTempImg1Eraser(None, init=True)

    def mousePressColorButton(self, event):
        posData = self.data[self.pos_i]
        items = list(self.checkedOverlayChannels)
        if len(items)>1:
            selectFluo = widgets.QDialogListbox(
                'Select image',
                'Select which fluorescence image you want to update the color of\n',
                items, multiSelection=False, parent=self
            )
            selectFluo.exec_()
            keys = selectFluo.selectedItemsText
            key = keys[0]
            if selectFluo.cancel or not keys:
                return
            else:
                self.overlayColorButton.channel = keys[0]
        else:
            self.overlayColorButton.channel = items[0]
        self.overlayColorButton.selectColor()

    def setEnabledCcaToolbar(self, enabled=False):
        self.manuallyEditCcaAction.setDisabled(False)
        self.viewCcaTableAction.setDisabled(False)
        self.ccaToolBar.setVisible(enabled)
        for action in self.ccaToolBar.actions():
            button = self.ccaToolBar.widgetForAction(action)
            action.setVisible(enabled)
            button.setEnabled(enabled)

    def setEnabledEditToolbarButton(self, enabled=False):
        for action in self.segmActions:
            action.setEnabled(enabled)

        for action in self.segmActionsVideo:
            action.setEnabled(enabled)

        self.SegmActionRW.setEnabled(enabled)
        self.relabelSequentialAction.setEnabled(enabled)
        self.repeatTrackingMenuAction.setEnabled(enabled)
        self.repeatTrackingVideoAction.setEnabled(enabled)
        self.postProcessSegmAction.setEnabled(enabled)
        self.autoSegmAction.setEnabled(enabled)
        self.editToolBar.setVisible(enabled)
        mode = self.modeComboBox.currentText()
        ccaON = mode == 'Cell cycle analysis'
        for action in self.editToolBar.actions():
            button = self.editToolBar.widgetForAction(action)
            # Keep binCellButton active in cca mode
            if button==self.binCellButton and not enabled and ccaON:
                action.setVisible(True)
                button.setEnabled(True)
            else:
                action.setVisible(enabled)
                button.setEnabled(enabled)
        if not enabled:
            self.setUncheckedAllButtons()

    def setEnabledFileToolbar(self, enabled):
        for action in self.fileToolBar.actions():
            button = self.fileToolBar.widgetForAction(action)
            if action == self.openAction or action == self.newAction:
                continue
            if action == self.manageVersionsAction:
                continue
            action.setEnabled(enabled)
            button.setEnabled(enabled)

    def reconnectUndoRedo(self):
        try:
            self.undoAction.triggered.disconnect()
            self.redoAction.triggered.disconnect()
        except Exception as e:
            pass
        mode = self.modeComboBox.currentText()
        if mode == 'Segmentation and Tracking' or mode == 'Snapshot':
            self.undoAction.triggered.connect(self.undo)
            self.redoAction.triggered.connect(self.redo)
        elif mode == 'Cell cycle analysis':
            self.undoAction.triggered.connect(self.UndoCca)
        elif mode == 'Custom annotations':
            self.undoAction.triggered.connect(self.undoCustomAnnotation)
        else:
            self.undoAction.setDisabled(True)
            self.redoAction.setDisabled(True)

    def enableSizeSpinbox(self, enabled):
        self.brushSizeLabelAction.setVisible(enabled)
        self.brushSizeAction.setVisible(enabled)
        self.brushAutoFillAction.setVisible(enabled)
        self.brushAutoHideAction.setVisible(enabled)
        self.brushEraserToolBar.setVisible(enabled)        
        self.disableNonFunctionalButtons()

    def reload_cb(self):
        posData = self.data[self.pos_i]
        # Store undo state before modifying stuff
        self.storeUndoRedoStates(False)
        labData = np.load(posData.segm_npz_path)
        # Keep compatibility with .npy and .npz files
        try:
            lab = labData['arr_0'][posData.frame_i]
        except Exception as e:
            lab = labData[posData.frame_i]
        posData.segm_data[posData.frame_i] = lab.copy()
        self.get_data()
        self.tracking()
        self.updateAllImages()

    def clearComboBoxFocus(self, mode):
        # Remove focus from modeComboBox to avoid the key_up changes its value
        self.sender().clearFocus()
        try:
            self.timer.stop()
            self.modeComboBox.setStyleSheet('background-color: none')
        except Exception as e:
            pass
    
    def updateModeMenuAction(self):
        self.modeActionGroup.triggered.disconnect()
        for action in self.modeActionGroup.actions():
            if action.text() != self.modeComboBox.currentText():
                continue
            action.setChecked(True)
            break
        self.modeActionGroup.triggered.connect(self.changeModeFromMenu)

    def changeModeFromMenu(self, action):
        self.modeComboBox.setCurrentText(action.text())

    def changeMode(self, idx):
        self.reconnectUndoRedo()
        self.updateModeMenuAction()
        posData = self.data[self.pos_i]
        mode = self.modeComboBox.itemText(idx)
        self.annotateToolbar.setVisible(False)
        self.store_data(autosave=False)
        if mode == 'Segmentation and Tracking':
            self.trackingMenu.setDisabled(False)
            self.modeToolBar.setVisible(True)
            self.initSegmTrackMode()
            self.setEnabledEditToolbarButton(enabled=True)
            self.addExistingDelROIs()
            self.checkTrackingEnabled()
            self.setEnabledCcaToolbar(enabled=False)
            self.realTimeTrackingToggle.setDisabled(False)
            self.realTimeTrackingToggle.label.setDisabled(False)
            if posData.cca_df is not None:
                self.store_cca_df()
        elif mode == 'Cell cycle analysis':
            proceed = self.initCca()
            if proceed:
                self.applyDelROIs()
            self.modeToolBar.setVisible(True)
            self.realTimeTrackingToggle.setDisabled(True)
            self.realTimeTrackingToggle.label.setDisabled(True)
            if proceed:
                self.setEnabledEditToolbarButton(enabled=False)
                if self.isSnapshot:
                    self.editToolBar.setVisible(True)
                self.setEnabledCcaToolbar(enabled=True)
                self.removeAlldelROIsCurrentFrame()
                self.annotCcaInfoCheckbox.setChecked(True)
                self.annotIDsCheckbox.setChecked(False)
                self.drawMothBudLinesCheckbox.setChecked(False)
                self.setDrawAnnotComboboxText()
                self.clearGhost()
        elif mode == 'Viewer':
            self.modeToolBar.setVisible(True)
            self.realTimeTrackingToggle.setDisabled(True)
            self.realTimeTrackingToggle.label.setDisabled(True)
            self.setEnabledEditToolbarButton(enabled=False)
            self.setEnabledCcaToolbar(enabled=False)
            self.removeAlldelROIsCurrentFrame()
            # currentMode = self.drawIDsContComboBox.currentText()
            # self.drawIDsContComboBox.clear()
            # self.drawIDsContComboBox.addItems(self.drawIDsContComboBoxCcaItems)
            # self.drawIDsContComboBox.setCurrentText(currentMode)
            self.navigateScrollBar.setMaximum(posData.SizeT)
            self.navSpinBox.setMaximum(posData.SizeT)
            self.clearGhost()
        elif mode == 'Custom annotations':
            self.modeToolBar.setVisible(True)
            self.realTimeTrackingToggle.setDisabled(True)
            self.realTimeTrackingToggle.label.setDisabled(True)
            self.setEnabledEditToolbarButton(enabled=False)
            self.setEnabledCcaToolbar(enabled=False)
            self.removeAlldelROIsCurrentFrame()
            self.annotateToolbar.setVisible(True)
            self.clearGhost()
        elif mode == 'Snapshot':
            self.reconnectUndoRedo()
            self.setEnabledSnapshotMode()

    def setEnabledSnapshotMode(self):
        posData = self.data[self.pos_i]
        self.manuallyEditCcaAction.setDisabled(False)
        self.viewCcaTableAction.setDisabled(False)
        for action in self.segmActions:
            action.setDisabled(False)
        self.SegmActionRW.setDisabled(False)
        if posData.SizeT == 1:
            self.segmVideoMenu.setDisabled(True)
        self.relabelSequentialAction.setDisabled(False)
        self.trackingMenu.setDisabled(True)
        self.postProcessSegmAction.setDisabled(False)
        self.autoSegmAction.setDisabled(False)
        self.ccaToolBar.setVisible(True)
        self.editToolBar.setVisible(True)
        self.modeToolBar.setVisible(False)
        self.reinitLastSegmFrameAction.setVisible(False)
        for action in self.ccaToolBar.actions():
            button = self.ccaToolBar.widgetForAction(action)
            if button == self.assignBudMothButton:
                button.setDisabled(False)
                action.setVisible(True)
            elif action == self.reInitCcaAction:
                action.setVisible(True)
            elif action == self.assignBudMothAutoAction and posData.SizeT==1:
                action.setVisible(True)
        for action in self.editToolBar.actions():
            button = self.editToolBar.widgetForAction(action)
            action.setVisible(True)
            button.setEnabled(True)
        self.realTimeTrackingToggle.setDisabled(True)
        self.realTimeTrackingToggle.label.setDisabled(True)
        self.repeatTrackingAction.setVisible(False)
        self.manualTrackingAction.setVisible(False)
        button = self.editToolBar.widgetForAction(self.repeatTrackingAction)
        button.setDisabled(True)
        button = self.editToolBar.widgetForAction(self.manualTrackingAction)
        button.setDisabled(True)
        self.disableNonFunctionalButtons()
        self.reinitLastSegmFrameAction.setVisible(False)

    def launchSlideshow(self):
        posData = self.data[self.pos_i]
        self.determineSlideshowWinPos()
        if self.slideshowButton.isChecked():
            self.slideshowWin = apps.imageViewer(
                parent=self,
                button_toUncheck=self.slideshowButton,
                linkWindow=posData.SizeT > 1,
                enableOverlay=True
            )
            h = self.drawIDsContComboBox.size().height()
            self.slideshowWin.framesScrollBar.setFixedHeight(h)
            self.slideshowWin.overlayButton.setChecked(
                self.overlayButton.isChecked()
            )
            self.slideshowWin.update_img()
            self.slideshowWin.show(
                left=self.slideshowWinLeft, top=self.slideshowWinTop
            )
        else:
            self.slideshowWin.close()
            self.slideshowWin = None
    
    def goToZsliceSearchedID(self, obj):
        if not self.isSegm3D:
            return
        
        current_z = self.z_lab()
        nearest_nonzero_z = core.nearest_nonzero_z_idx_from_z_centroid(
            obj, current_z=current_z
        )
        if nearest_nonzero_z == current_z:
            self.drawPointsLayers(computePointsLayers=True)
            return
        
        self.zSliceScrollBar.setSliderPosition(nearest_nonzero_z)
        self.update_z_slice(nearest_nonzero_z)

    def nearest_nonzero(self, a, y, x):
        r, c = np.nonzero(a)
        dist = ((r - y)**2 + (c - x)**2)
        min_idx = dist.argmin()
        return a[r[min_idx], c[min_idx]]

    def convexity_defects(self, img, eps_percent):
        img = img.astype(np.uint8)
        contours, _ = cv2.findContours(img,2,1)
        cnt = contours[0]
        cnt = cv2.approxPolyDP(cnt,eps_percent*cv2.arcLength(cnt,True),True) # see https://www.programcreek.com/python/example/89457/cv22.convexityDefects
        hull = cv2.convexHull(cnt,returnPoints = False) # see https://opencv-python-tutroals.readthedocs.io/en/latest/py_tutorials/py_imgproc/py_contours/py_contours_more_functions/py_contours_more_functions.html
        defects = cv2.convexityDefects(cnt,hull) # see https://opencv-python-tutroals.readthedocs.io/en/latest/py_tutorials/py_imgproc/py_contours/py_contours_more_functions/py_contours_more_functions.html
        return cnt, defects

    def auto_separate_bud_ID(
            self, ID, lab, rp, max_ID, max_i=1, enforce=False, 
            eps_percent=0.01
        ):
        lab_ID_bool = lab == ID
        # First try separating by labelling
        lab_ID = lab_ID_bool.astype(int)
        rp_ID = skimage.measure.regionprops(lab_ID)
        setRp = self.separateByLabelling(lab_ID, rp_ID, maxID=max_ID)
        if setRp:
            success = True
            lab[lab_ID_bool] = lab_ID[lab_ID_bool]
            return lab, success

        cnt, defects = self.convexity_defects(lab_ID_bool, eps_percent)
        success = False
        if defects is not None:
            if len(defects) == 2:
                num_obj_watshed = 0
                # Separate only if it was a separation also with watershed method
                if num_obj_watshed > 2 or enforce:
                    defects_points = [0]*len(defects)
                    for i, defect in enumerate(defects):
                        s,e,f,d = defect[0]
                        x,y = tuple(cnt[f][0])
                        defects_points[i] = (y,x)
                    (r0, c0), (r1, c1) = defects_points
                    rr, cc, _ = skimage.draw.line_aa(r0, c0, r1, c1)
                    sep_bud_img = np.copy(lab_ID_bool)
                    sep_bud_img[rr, cc] = False
                    sep_bud_label = skimage.measure.label(
                                               sep_bud_img, connectivity=2)
                    rp_sep = skimage.measure.regionprops(sep_bud_label)
                    IDs_sep = [obj.label for obj in rp_sep]
                    areas = [obj.area for obj in rp_sep]
                    curr_ID_bud = IDs_sep[areas.index(min(areas))]
                    curr_ID_moth = IDs_sep[areas.index(max(areas))]
                    orig_sblab = np.copy(sep_bud_label)
                    # sep_bud_label = np.zeros_like(sep_bud_label)
                    sep_bud_label[orig_sblab==curr_ID_moth] = ID
                    sep_bud_label[orig_sblab==curr_ID_bud] = max_ID+max_i
                    # sep_bud_label *= (max_ID+max_i)
                    temp_sep_bud_lab = sep_bud_label.copy()
                    for r, c in zip(rr, cc):
                        if lab_ID_bool[r, c]:
                            nearest_ID = self.nearest_nonzero(
                                                    sep_bud_label, r, c)
                            temp_sep_bud_lab[r,c] = nearest_ID
                    sep_bud_label = temp_sep_bud_lab
                    sep_bud_label_mask = sep_bud_label != 0
                    # plt.imshow_tk(sep_bud_label, dots_coords=np.asarray(defects_points))
                    lab[sep_bud_label_mask] = sep_bud_label[sep_bud_label_mask]
                    max_i += 1
                    success = True
        return lab, success

    def disconnectLeftClickButtons(self):
        for button in self.LeftClickButtons:
            try:
                button.toggled.disconnect()
            except Exception as e:
                # Not all the LeftClickButtons have toggled connected
                pass

    def uncheckLeftClickButtons(self, sender):
        for button in self.LeftClickButtons:
            if button != sender:
                button.setChecked(False)
        
        if button != self.labelRoiButton:
            # self.labelRoiButton is disconnected so we manually call uncheck
            self.labelRoi_cb(False)
        self.wandControlsToolbar.setVisible(False)
        self.enableSizeSpinbox(False)
        if sender is not None:
            self.keepIDsButton.setChecked(False)

    def connectLeftClickButtons(self):
        self.brushButton.toggled.connect(self.Brush_cb)
        self.curvToolButton.toggled.connect(self.curvTool_cb)
        self.rulerButton.toggled.connect(self.ruler_cb)
        self.eraserButton.toggled.connect(self.Eraser_cb)
        self.wandToolButton.toggled.connect(self.wand_cb)
        self.labelRoiButton.toggled.connect(self.labelRoi_cb)
        self.expandLabelToolButton.toggled.connect(self.expandLabelCallback)
        self.addDelPolyLineRoiAction.toggled.connect(self.addDelPolyLineRoi_cb)
        for action in self.pointsLayersToolbar.actions()[1:]:
            if not hasattr(action, 'layerTypeIdx'):
                continue
            if action.layerTypeIdx != 4:
                continue
            action.button.toggled.connect(
                self.addPointsByClickingButtonToggled
            )

    def brushSize_cb(self, value):
        self.ax2_EraserCircle.setSize(value*2)
        self.ax1_BrushCircle.setSize(value*2)
        self.ax2_BrushCircle.setSize(value*2)
        self.ax1_EraserCircle.setSize(value*2)
        self.ax2_EraserX.setSize(value)
        self.ax1_EraserX.setSize(value)
        self.setDiskMask()
    
    def autoIDtoggled(self, checked):
        self.editIDspinboxAction.setDisabled(checked)
        self.editIDLabelAction.setDisabled(checked)
        if not checked and self.editIDspinbox.value() == 0:
            newID = self.setBrushID(return_val=True)
            self.editIDspinbox.setValue(newID)

    def wand_cb(self, checked):
        posData = self.data[self.pos_i]
        if checked:
            self.disconnectLeftClickButtons()
            self.uncheckLeftClickButtons(self.wandToolButton)
            self.connectLeftClickButtons()
            self.wandControlsToolbar.setVisible(True)
        else:
            self.resetCursors()
            self.wandControlsToolbar.setVisible(False)
        if not self.labelsGrad.showLabelsImgAction.isChecked():
            self.ax1.autoRange()
    
    def lebelRoiTrangeCheckboxToggled(self, checked):
        disabled = not checked
        self.labelRoiStartFrameNoSpinbox.setDisabled(disabled)
        self.labelRoiStopFrameNoSpinbox.setDisabled(disabled)
        self.labelRoiStartFrameNoSpinbox.label.setDisabled(disabled)
        self.labelRoiStopFrameNoSpinbox.label.setDisabled(disabled)
        self.labelRoiToEndFramesAction.setDisabled(disabled)
        self.labelRoiFromCurrentFrameAction.setDisabled(disabled)

        if disabled:
            return

        posData = self.data[self.pos_i]

        self.labelRoiStartFrameNoSpinbox.setValue(posData.frame_i+1)
        self.labelRoiStopFrameNoSpinbox.setValue(posData.SizeT)

    def labelRoi_cb(self, checked):
        posData = self.data[self.pos_i]
        if checked:
            self.disconnectLeftClickButtons()
            self.uncheckLeftClickButtons(self.labelRoiButton)
            self.connectLeftClickButtons()

            self.labelRoiStartFrameNoSpinbox.setMaximum(posData.SizeT)
            self.labelRoiStopFrameNoSpinbox.setMaximum(posData.SizeT)

            if self.labelRoiActiveWorkers:
                lastActiveWorker = self.labelRoiActiveWorkers[-1]
                self.labelRoiGarbageWorkers.append(lastActiveWorker)
                lastActiveWorker.finished.emit()
                self.logger.info('Collected garbage w5orker (magic labeller).')
            
            self.labelRoiToolbar.setVisible(True)
            if self.isSegm3D:
                self.labelRoiZdepthSpinbox.setDisabled(False)
            else:
                self.labelRoiZdepthSpinbox.setDisabled(True)

            # Start thread and pause it
            self.labelRoiThread = QThread()
            self.labelRoiMutex = QMutex()
            self.labelRoiWaitCond = QWaitCondition()

            labelRoiWorker = workers.LabelRoiWorker(self)

            labelRoiWorker.moveToThread(self.labelRoiThread)
            labelRoiWorker.finished.connect(self.labelRoiThread.quit)
            labelRoiWorker.finished.connect(labelRoiWorker.deleteLater)
            self.labelRoiThread.finished.connect(
                self.labelRoiThread.deleteLater
            )

            labelRoiWorker.finished.connect(self.labelRoiWorkerFinished)
            labelRoiWorker.sigLabellingDone.connect(self.labelRoiDone)
            labelRoiWorker.sigProgressBar.connect(self.workerUpdateProgressbar)

            labelRoiWorker.progress.connect(self.workerProgress)
            labelRoiWorker.critical.connect(self.workerCritical)

            self.labelRoiActiveWorkers.append(labelRoiWorker)

            self.labelRoiThread.started.connect(labelRoiWorker.run)
            self.labelRoiThread.start()

            # Add the rectROI to ax1
            self.ax1.addItem(self.labelRoiItem)
        else:
            self.labelRoiToolbar.setVisible(False)
            
            for worker in self.labelRoiActiveWorkers:
                worker._stop()
            while self.app.overrideCursor() is not None:
                self.app.restoreOverrideCursor()
            
            self.labelRoiItem.setPos((0,0))
            self.labelRoiItem.setSize((0,0))
            self.freeRoiItem.clear()
            self.ax1.removeItem(self.labelRoiItem)
            self.updateLabelRoiCircularCursor(None, None, False)
    
    def labelRoiWorkerFinished(self):
        self.logger.info('Magic labeller closed.')
        worker = self.labelRoiActiveWorkers.pop(-1)
    
    def indexRoiLab(self, roiLab, roiLabSlice, lab, brushID):
        # Delete only objects touching borders in X and Y not in Z
        if self.labelRoiAutoClearBorderCheckbox.isChecked():
            mask = np.zeros(roiLab.shape, dtype=bool)
            mask[..., 1:-1, 1:-1] = True
            roiLab = skimage.segmentation.clear_border(roiLab, mask=mask)

        roiLabMask = roiLab>0
        roiLab[roiLabMask] += (brushID-1)
        if self.labelRoiReplaceExistingObjectsCheckbox.isChecked():
            IDs_touched_by_new_objects = np.unique(lab[roiLabSlice][roiLabMask])
            for ID in IDs_touched_by_new_objects:
                lab[lab==ID] = 0
        
        lab[roiLabSlice][roiLabMask] = roiLab[roiLabMask]
        return lab

    @exception_handler
    def labelRoiDone(self, roiSegmData, isTimeLapse):
        posData = self.data[self.pos_i]
        self.setBrushID()

        if isTimeLapse:
            self.progressWin.mainPbar.setMaximum(0)
            self.progressWin.mainPbar.setValue(0)
            current_frame_i = posData.frame_i
            start_frame_i = self.labelRoiStartFrameNoSpinbox.value() - 1
            for i, roiLab in enumerate(roiSegmData):
                frame_i = start_frame_i + i
                lab = posData.allData_li[frame_i]['labels']
                store = True
                if lab is None:
                    if frame_i >= len(posData.segm_data):
                        lab = np.zeros_like(posData.segm_data[0])
                        posData.segm_data = np.append(
                            posData.segm_data, lab[np.newaxis], axis=0
                        )
                    else:
                        lab = posData.segm_data[frame_i]
                    store = False
                roiLabSlice = self.labelRoiSlice[1:]
                lab = self.indexRoiLab(
                    roiLab, roiLabSlice, lab, posData.brushID
                )
                if store:
                    posData.frame_i = frame_i
                    posData.allData_li[frame_i]['labels'] = lab.copy()
                    self.get_data()
                    self.store_data(autosave=False)
            
            # Back to current frame
            posData.frame_i = current_frame_i
            self.get_data()
        else:
            roiLab = roiSegmData
            posData.lab = self.indexRoiLab(
                roiLab, self.labelRoiSlice, posData.lab, posData.brushID
            )

        self.update_rp()
        
        # Repeat tracking
        if self.editIDcheckbox.isChecked():
            self.tracking(enforce=True, assign_unique_new_IDs=False)
        
        self.store_data()
        self.updateAllImages()
        
        self.labelRoiItem.setPos((0,0))
        self.labelRoiItem.setSize((0,0))
        self.freeRoiItem.clear()
        self.logger.info('Magic labeller done!')
        self.app.restoreOverrideCursor()  

        self.labelRoiRunning = False    
        if self.progressWin is not None:
            self.progressWin.workerFinished = True
            self.progressWin.close()
            self.progressWin = None  

    def restoreHoverObjBrush(self):
        posData = self.data[self.pos_i]
        if self.ax1BrushHoverID in posData.IDs:
            obj_idx = posData.IDs_idxs[self.ax1BrushHoverID]
            obj = posData.rp[obj_idx]
            self.addObjContourToContoursImage(obj=obj, ax=0)
            self.addObjContourToContoursImage(obj=obj, ax=1)

    def hideItemsHoverBrush(self, xy=None, ID=None, force=False):
        if xy is not None:
            x, y = xy
            if x is None:
                return

            xdata, ydata = int(x), int(y)
            Y, X = self.currentLab2D.shape

            if not (xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y):
                return

        if not self.brushAutoHideCheckbox.isChecked() and not force:
            return
        
        posData = self.data[self.pos_i]
        size = self.brushSizeSpinbox.value()*2

        if xy is not None:
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]

        if self.ax1_lostObjScatterItem.isVisible():
            self.ax1_lostObjScatterItem.setVisible(False)
        
        if self.ax2_lostObjScatterItem.isVisible():
            self.ax2_lostObjScatterItem.setVisible(False)

        # Restore ID previously hovered
        if ID != self.ax1BrushHoverID and not self.isMouseDragImg1:
            self.restoreHoverObjBrush()

        # Hide items hover ID
        if ID != 0:
            self.clearObjContour(ID=ID, ax=0)
            self.clearObjContour(ID=ID, ax=1)
            # self.setAllTextAnnotations(labelsToSkip={ID:True})
            self.ax1BrushHoverID = ID
        else:
            # self.setAllTextAnnotations()
            self.ax1BrushHoverID = 0

    def updateBrushCursor(self, x, y):
        if x is None:
            return

        xdata, ydata = int(x), int(y)
        _img = self.currentLab2D
        Y, X = _img.shape

        if not (xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y):
            return

        posData = self.data[self.pos_i]
        size = self.brushSizeSpinbox.value()*2
        self.setHoverToolSymbolData(
            [x], [y], (self.ax2_BrushCircle, self.ax1_BrushCircle),
            size=size
        )
        self.setHoverToolSymbolColor(
            xdata, ydata, self.ax2_BrushCirclePen,
            (self.ax2_BrushCircle, self.ax1_BrushCircle),
            self.brushButton, brush=self.ax2_BrushCircleBrush
        )
    
    def moveLabelButtonToggled(self, checked):
        if not checked:
            self.hoverLabelID = 0
            self.highlightedID = 0
            self.highLightIDLayerImg1.clear()
            self.highLightIDLayerRightImage.clear()
            self.highlightIDcheckBoxToggled(False)
    
    def setAllIDs(self):
        for posData in self.data:
            posData.allIDs = set()
            for frame_i in range(len(posData.segm_data)):
                lab = posData.allData_li[frame_i]['labels']
                if lab is None:
                    rp = skimage.measure.regionprops(posData.segm_data[frame_i])
                else:
                    rp = posData.allData_li[frame_i]['regionprops']
                posData.allIDs.update([obj.label for obj in rp])
    
    def keepIDs_cb(self, checked):
        self.keepIDsToolbar.setVisible(checked)
        if checked:
            if self.annotCcaInfoCheckbox.isChecked():
                self.annotCcaInfoCheckbox.setChecked(False)
                self.annotIDsCheckbox.setChecked(True)
                self.setDrawAnnotComboboxText()
            self.uncheckLeftClickButtons(None)
            self.initKeepObjLabelsLayers()      
            self.setAllIDs()
        else:
            # restore items to non-grayed out
            self.tempLayerImg1.setImage(self.emptyLab, autoLevels=False)
            self.tempLayerRightImage.setImage(self.emptyLab, autoLevels=False)
            alpha = self.imgGrad.labelsAlphaSlider.value()
            self.labelsLayerImg1.setOpacity(alpha)
            self.labelsLayerRightImg.setOpacity(alpha)
            self.ax1_contoursImageItem.setOpacity(1.0)
            self.ax2_contoursImageItem.setOpacity(1.0)
        
        self.highlightedIDopts = None
        self.keptObjectsIDs = widgets.KeptObjectIDsList(
            self.keptIDsLineEdit, self.keepIDsConfirmAction
        )
        self.updateAllImages()

        QTimer.singleShot(300, self.autoRange)

    def Brush_cb(self, checked):
        if checked:
            self.typingEditID = False
            self.setDiskMask()
            self.setHoverToolSymbolData(
                [], [], (self.ax1_EraserCircle, self.ax2_EraserCircle,
                         self.ax1_EraserX, self.ax2_EraserX)
            )
            self.updateBrushCursor(self.xHoverImg, self.yHoverImg)
            self.setBrushID()

            self.disconnectLeftClickButtons()
            self.uncheckLeftClickButtons(self.sender())
            c = self.defaultToolBarButtonColor
            self.eraserButton.setStyleSheet(f'background-color: {c}')
            self.connectLeftClickButtons()
            self.enableSizeSpinbox(True)
            self.showEditIDwidgets(True)
        else:
            self.ax1_lostObjScatterItem.setVisible(True)
            self.ax2_lostObjScatterItem.setVisible(True)
            self.setHoverToolSymbolData(
                [], [], (self.ax2_BrushCircle, self.ax1_BrushCircle),
            )
            self.enableSizeSpinbox(False)
            self.showEditIDwidgets(False)
            self.resetCursors()
    
    def showEditIDwidgets(self, visible):
        self.editIDLabelAction.setVisible(visible)
        self.editIDspinboxAction.setVisible(visible)
        self.editIDcheckboxAction.setVisible(visible)
    
    def resetCursors(self):
        self.ax1_cursor.setData([], [])
        self.ax2_cursor.setData([], [])
        while self.app.overrideCursor() is not None:
            self.app.restoreOverrideCursor()

    def setDiskMask(self):
        brushSize = self.brushSizeSpinbox.value()
        # diam = brushSize*2
        # center = (brushSize, brushSize)
        # diskShape = (diam+1, diam+1)
        # diskMask = np.zeros(diskShape, bool)
        # rr, cc = skimage.draw.disk(center, brushSize+1, shape=diskShape)
        # diskMask[rr, cc] = True
        self.diskMask = skimage.morphology.disk(brushSize, dtype=bool)

    def getDiskMask(self, xdata, ydata):
        Y, X = self.currentLab2D.shape[-2:]

        brushSize = self.brushSizeSpinbox.value()
        yBottom, xLeft = ydata-brushSize, xdata-brushSize
        yTop, xRight = ydata+brushSize+1, xdata+brushSize+1

        if xLeft<0:
            if yBottom<0:
                # Disk mask out of bounds top-left
                diskMask = self.diskMask.copy()
                diskMask = diskMask[-yBottom:, -xLeft:]
                yBottom = 0
            elif yTop>Y:
                # Disk mask out of bounds bottom-left
                diskMask = self.diskMask.copy()
                diskMask = diskMask[0:Y-yBottom, -xLeft:]
                yTop = Y
            else:
                # Disk mask out of bounds on the left
                diskMask = self.diskMask.copy()
                diskMask = diskMask[:, -xLeft:]
            xLeft = 0

        elif xRight>X:
            if yBottom<0:
                # Disk mask out of bounds top-right
                diskMask = self.diskMask.copy()
                diskMask = diskMask[-yBottom:, 0:X-xLeft]
                yBottom = 0
            elif yTop>Y:
                # Disk mask out of bounds bottom-right
                diskMask = self.diskMask.copy()
                diskMask = diskMask[0:Y-yBottom, 0:X-xLeft]
                yTop = Y
            else:
                # Disk mask out of bounds on the right
                diskMask = self.diskMask.copy()
                diskMask = diskMask[:, 0:X-xLeft]
            xRight = X

        elif yBottom<0:
            # Disk mask out of bounds on top
            diskMask = self.diskMask.copy()
            diskMask = diskMask[-yBottom:]
            yBottom = 0

        elif yTop>Y:
            # Disk mask out of bounds on bottom
            diskMask = self.diskMask.copy()
            diskMask = diskMask[0:Y-yBottom]
            yTop = Y

        else:
            # Disk mask fully inside the image
            diskMask = self.diskMask

        return yBottom, xLeft, yTop, xRight, diskMask

    def setBrushID(self, useCurrentLab=True, return_val=False):
        # Make sure that the brushed ID is always a new one based on
        # already visited frames
        posData = self.data[self.pos_i]
        if useCurrentLab:
            newID = max(posData.IDs, default=1)
        else:
            newID = 1
        for frame_i, storedData in enumerate(posData.allData_li):
            if frame_i == posData.frame_i:
                continue
            lab = storedData['labels']
            if lab is not None:
                rp = storedData['regionprops']
                _max = max([obj.label for obj in rp], default=0)
                if _max > newID:
                    newID = _max
            else:
                break

        for y, x, manual_ID in posData.editID_info:
            if manual_ID > newID:
                newID = manual_ID
        posData.brushID = newID+1
        if return_val:
            return posData.brushID

    def equalizeHist(self):
        # Store undo state before modifying stuff
        self.storeUndoRedoStates(False, storeImage=True)
        self.updateAllImages()

    def curvTool_cb(self, checked):
        posData = self.data[self.pos_i]
        if checked:
            self.disconnectLeftClickButtons()
            self.uncheckLeftClickButtons(self.curvToolButton)
            self.connectLeftClickButtons()
            self.hoverLinSpace = np.linspace(0, 1, 1000)
            self.curvPlotItem = pg.PlotDataItem(pen=self.newIDs_cpen)
            self.curvHoverPlotItem = pg.PlotDataItem(pen=self.oldIDs_cpen)
            self.curvAnchors = pg.ScatterPlotItem(
                symbol='o', size=9,
                brush=pg.mkBrush((255,0,0,50)),
                pen=pg.mkPen((255,0,0), width=2),
                hoverable=True, hoverPen=pg.mkPen((255,0,0), width=3),
                hoverBrush=pg.mkBrush((255,0,0)), tip=None
            )
            self.ax1.addItem(self.curvAnchors)
            self.ax1.addItem(self.curvPlotItem)
            self.ax1.addItem(self.curvHoverPlotItem)
            self.splineHoverON = True
            posData.curvPlotItems.append(self.curvPlotItem)
            posData.curvAnchorsItems.append(self.curvAnchors)
            posData.curvHoverItems.append(self.curvHoverPlotItem)
        else:
            self.splineHoverON = False
            self.isRightClickDragImg1 = False
            self.clearCurvItems()
            while self.app.overrideCursor() is not None:
                self.app.restoreOverrideCursor()

    def updateHoverLabelCursor(self, x, y):
        if x is None:
            self.hoverLabelID = 0
            return

        xdata, ydata = int(x), int(y)
        Y, X = self.currentLab2D.shape
        if not (xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y):
            return

        ID = self.currentLab2D[ydata, xdata]
        self.hoverLabelID = ID

        if ID == 0:
            if self.highlightedID != 0:
                self.updateAllImages()
                self.highlightedID = 0
            return

        if self.app.overrideCursor() != Qt.SizeAllCursor:
            self.app.setOverrideCursor(Qt.SizeAllCursor)
        
        if not self.isMovingLabel:
            self.highlightSearchedID(ID)

    def updateEraserCursor(self, x, y):
        if x is None:
            return

        xdata, ydata = int(x), int(y)
        _img = self.currentLab2D
        Y, X = _img.shape
        posData = self.data[self.pos_i]

        if not (xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y):
            return

        posData = self.data[self.pos_i]
        size = self.brushSizeSpinbox.value()*2
        self.setHoverToolSymbolData(
            [x], [y], (self.ax1_EraserCircle, self.ax2_EraserCircle),
            size=size
        )
        self.setHoverToolSymbolData(
            [x], [y], (self.ax1_EraserX, self.ax2_EraserX),
            size=int(size/2)
        )

        isMouseDrag = (
            self.isMouseDragImg1 or self.isMouseDragImg2
        )

        if not isMouseDrag:
            self.setHoverToolSymbolColor(
                xdata, ydata, self.eraserCirclePen,
                (self.ax2_EraserCircle, self.ax1_EraserCircle),
                self.eraserButton, hoverRGB=None
            )

    def Eraser_cb(self, checked):
        if checked:
            self.setDiskMask()
            self.setHoverToolSymbolData(
                [], [], (self.ax2_BrushCircle, self.ax1_BrushCircle),
            )
            self.updateEraserCursor(self.xHoverImg, self.yHoverImg)
            self.disconnectLeftClickButtons()
            self.uncheckLeftClickButtons(self.sender())
            c = self.defaultToolBarButtonColor
            self.brushButton.setStyleSheet(f'background-color: {c}')
            self.connectLeftClickButtons()
            self.enableSizeSpinbox(True)
        else:
            self.setHoverToolSymbolData(
                [], [], (self.ax1_EraserCircle, self.ax2_EraserCircle,
                         self.ax1_EraserX, self.ax2_EraserX)
            )
            self.enableSizeSpinbox(False)
            self.resetCursors()
            self.updateAllImages()
    
    def storeCurrentAnnotOptions_ax1(self):
        checkboxes = [
            'annotIDsCheckbox',
            'annotCcaInfoCheckbox',
            'annotContourCheckbox',
            'annotSegmMasksCheckbox',
            'drawMothBudLinesCheckbox',
            'annotNumZslicesCheckbox',
            'drawNothingCheckbox',
        ]
        self.annotOptions = {}
        for checkboxName in checkboxes:
            checkbox = getattr(self, checkboxName)
            self.annotOptions[checkboxName] = checkbox.isChecked()

    def storeCurrentAnnotOptions_ax2(self):
        checkboxes = [
            'annotIDsCheckboxRight',
            'annotCcaInfoCheckboxRight',
            'annotContourCheckboxRight',
            'annotSegmMasksCheckboxRight',
            'drawMothBudLinesCheckboxRight',
            'annotNumZslicesCheckboxRight',
            'drawNothingCheckboxRight',
        ]
        self.annotOptionsRight = {}
        for checkboxName in checkboxes:
            checkbox = getattr(self, checkboxName)
            self.annotOptionsRight[checkboxName] = checkbox.isChecked()
    
    def restoreAnnotOptions_ax1(self):
        if not hasattr(self, 'annotOptions'):
            return

        for option, state in self.annotOptions.items():
            checkbox = getattr(self, option)
            checkbox.setChecked(state)
        
        self.setDrawAnnotComboboxText()
    
    def restoreAnnotOptions_ax2(self):
        if not hasattr(self, 'annotOptionsRight'):
            return

        for option, state in self.annotOptionsRight.items():
            checkbox = getattr(self, option)
            checkbox.setChecked(state)
        
        self.setDrawAnnotComboboxText()

    def onDoubleSpaceBar(self):
        how = self.drawIDsContComboBox.currentText()
        if how.find('nothing') == -1:
            self.storeCurrentAnnotOptions_ax1()
            self.drawNothingCheckbox.setChecked(True)
            self.annotOptionClicked(sender=self.drawNothingCheckbox)
        else:
            self.restoreAnnotOptions_ax1()
        
        how = self.annotateRightHowCombobox.currentText()
        if how.find('nothing') == -1:
            self.storeCurrentAnnotOptions_ax2()
            self.drawNothingCheckboxRight.setChecked(True)
            self.annotOptionClickedRight(sender=self.drawNothingCheckboxRight)
        else:
            self.restoreAnnotOptions_ax2()


    def resizeBottomLayoutLineClicked(self, event):
        pass
        
    def resizeBottomLayoutLineDragged(self, event):
        if not self.img1BottomGroupbox.isVisible():
            return
        newBottomLayoutHeight = self.bottomScrollArea.minimumHeight() - event.y()
        self.bottomScrollArea.setFixedHeight(newBottomLayoutHeight)
    
    def resizeBottomLayoutLineReleased(self):
        QTimer.singleShot(100, self.autoRange)
    
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            pos = self.resizeBottomLayoutLine.mapFromGlobal(event.globalPos())
            if pos.y()>=0:
                self.gui_raiseBottomLayoutContextMenu(event)
        return super().mousePressEvent(event)
        
    def zoomBottomLayoutActionTriggered(self, checked):
        if not checked:
            return
        perc = int(re.findall(r'(\d+)%', self.sender().text())[0])
        if perc != 100:
            fontSizeFactor = perc/100
            heightFactor = perc/100
            self.resizeSlidersArea(
                fontSizeFactor=fontSizeFactor, heightFactor=heightFactor
            )
        else:
            self.gui_resetBottomLayoutHeight()
        self.df_settings.at['bottom_sliders_zoom_perc', 'value'] = perc
        self.df_settings.to_csv(self.settings_csv_path)
        QTimer.singleShot(150, self.resizeGui)
    
    def retainSpaceSlidersToggled(self, checked):
        if checked:
            self.df_settings.at['retain_space_hidden_sliders', 'value'] = 'Yes'
        else:
            self.df_settings.at['retain_space_hidden_sliders', 'value'] = 'No'
        self.df_settings.to_csv(self.settings_csv_path)
        if not self.zSliceScrollBar.isEnabled():
            retainSpaceZ = False
        else:
            retainSpaceZ = checked
        myutils.setRetainSizePolicy(self.zSliceScrollBar, retain=retainSpaceZ)
        myutils.setRetainSizePolicy(self.zProjComboBox, retain=retainSpaceZ)
        myutils.setRetainSizePolicy(self.zSliceOverlay_SB, retain=retainSpaceZ)
        myutils.setRetainSizePolicy(self.zProjOverlay_CB, retain=retainSpaceZ)
        myutils.setRetainSizePolicy(self.overlay_z_label, retain=retainSpaceZ)
        
        # for overlayItems in self.overlayLayersItems.values():
        #     alphaScrollBar = overlayItems[2]
        #     myutils.setRetainSizePolicy(alphaScrollBar, retain=checked)
        #     myutils.setRetainSizePolicy(alphaScrollBar.label, retain=checked)
        
        QTimer.singleShot(200, self.resizeGui)

    @exception_handler
    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Q:
            # self.setAllIDs()
            posData = self.data[self.pos_i]
            # from acdctools.plot import imshow
            # delIDs = posData.allData_li[posData.frame_i]['delROIs_info']['delIDsROI']
            # printl(delIDs)
            # self.store_data()
            # self.applyDelROIs()
            # stored_lab = posData.allData_li[posData.frame_i]['labels']
            # imshow(posData.lab, stored_lab, parent=self)
        
        if not self.dataIsLoaded:
            self.logger.info(
                '[WARNING]: Data not loaded yet. '
                'Key pressing events are not connected.'
            )
            return
        if ev.key() == Qt.Key_Control:
            self.isCtrlDown = True
        
        modifiers = ev.modifiers()
        isAltModifier = modifiers == Qt.AltModifier
        isCtrlModifier = modifiers == Qt.ControlModifier
        isShiftModifier = modifiers == Qt.ShiftModifier
        self.isZmodifier = (
            ev.key()== Qt.Key_Z and not isAltModifier
            and not isCtrlModifier and not isShiftModifier
        )
        if isShiftModifier:
            self.isShiftDown = True
            if self.brushButton.isChecked():
                # Force default brush symbol with shift down
                self.setHoverToolSymbolColor(
                    1, 1, self.ax2_BrushCirclePen,
                    (self.ax2_BrushCircle, self.ax1_BrushCircle),
                    self.brushButton, brush=self.ax2_BrushCircleBrush,
                    ID=0
                )
            if self.isSegm3D:
                self.changeBrushID()        
        isBrushActive = (
            self.brushButton.isChecked() or self.eraserButton.isChecked()
        )
        isManualTrackingActive = self.manualTrackingButton.isChecked()
        if self.brushButton.isChecked():
            try:
                n = int(ev.text())
                if self.editIDcheckbox.isChecked():
                    self.editIDcheckbox.setChecked(False)
                if self.typingEditID:
                    ID = int(f'{self.editIDspinbox.value()}{n}')
                else:
                    ID = n
                    self.typingEditID = True
                self.editIDspinbox.setValue(ID)
            except Exception as e:
                # printl(traceback.format_exc())
                pass
        
        if self.manualTrackingButton.isChecked():
            try:
                n = int(ev.text())
                if self.typingEditID:
                    ID = int(f'{self.manualTrackingToolbar.spinboxID.value()}{n}')
                else:
                    ID = n
                    self.typingEditID = True
                self.manualTrackingToolbar.spinboxID.setValue(ID)
            except Exception as e:
                # printl(traceback.format_exc())
                pass
        
        isBrushKey = ev.key() == self.brushButton.keyPressShortcut
        isEraserKey = ev.key() == self.eraserButton.keyPressShortcut
        isExpandLabelActive = self.expandLabelToolButton.isChecked()
        isWandActive = self.wandToolButton.isChecked()
        isLabelRoiCircActive = (
            self.labelRoiButton.isChecked() 
            and self.labelRoiIsCircularRadioButton.isChecked()
        )
        how = self.drawIDsContComboBox.currentText()
        isOverlaySegm = how.find('overlay segm. masks') != -1
        if ev.key()==Qt.Key_Up and not isCtrlModifier:
            isAutoPilotActive = (
                self.autoPilotZoomToObjToggle.isChecked()
                and self.autoPilotZoomToObjToolbar.isVisible()
            )
            if isBrushActive:
                brushSize = self.brushSizeSpinbox.value()
                self.brushSizeSpinbox.setValue(brushSize+1)
            elif isWandActive:
                wandTolerance = self.wandToleranceSlider.value()
                self.wandToleranceSlider.setValue(wandTolerance+1)
            elif isExpandLabelActive:
                self.expandLabel(dilation=True)
                self.expandFootprintSize += 1
            elif isLabelRoiCircActive:
                val = self.labelRoiCircularRadiusSpinbox.value()
                self.labelRoiCircularRadiusSpinbox.setValue(val+1)
            elif isAutoPilotActive:
                self.pointsLayerAutoPilot('next')
            else:
                self.zSliceScrollBar.triggerAction(
                    QAbstractSlider.SliderAction.SliderSingleStepAdd
                )
        elif ev.key()==Qt.Key_Down and not isCtrlModifier:
            isAutoPilotActive = (
                self.autoPilotZoomToObjToggle.isChecked()
                and self.autoPilotZoomToObjToolbar.isVisible()
            )
            if isBrushActive:
                brushSize = self.brushSizeSpinbox.value()
                self.brushSizeSpinbox.setValue(brushSize-1)
            elif isWandActive:
                wandTolerance = self.wandToleranceSlider.value()
                self.wandToleranceSlider.setValue(wandTolerance-1)
            elif isExpandLabelActive:
                self.expandLabel(dilation=False)
                self.expandFootprintSize += 1
            elif isLabelRoiCircActive:
                val = self.labelRoiCircularRadiusSpinbox.value()
                self.labelRoiCircularRadiusSpinbox.setValue(val-1)
            elif isAutoPilotActive:
                self.pointsLayerAutoPilot('prev')
            else:
                self.zSliceScrollBar.triggerAction(
                    QAbstractSlider.SliderAction.SliderSingleStepSub
                )
        # elif ev.key()==Qt.Key_Left and not isCtrlModifier:
        #     self.prev_cb()
        # elif ev.key()==Qt.Key_Right and not isCtrlModifier:
        #     self.next_cb()
        elif ev.key() == Qt.Key_Enter or ev.key() == Qt.Key_Return:
            if self.brushButton.isChecked() or isManualTrackingActive:
                self.typingEditID = False
            elif self.keepIDsButton.isChecked():
                self.keepIDsConfirmAction.trigger()
        elif ev.key() == Qt.Key_Escape:
            if self.keepIDsButton.isChecked() and self.keptObjectsIDs:
                self.keptObjectsIDs = widgets.KeptObjectIDsList(
                    self.keptIDsLineEdit, self.keepIDsConfirmAction
                )
                self.highlightHoverIDsKeptObj(0, 0, hoverID=0)
                return

            if self.brushButton.isChecked() and self.typingEditID:
                self.editIDcheckbox.setChecked(True)
                self.typingEditID = False
                return
            
            if isManualTrackingActive and self.typingEditID:
                self.typingEditID = False
                return
            
            if self.labelRoiButton.isChecked() and self.isMouseDragImg1:
                self.isMouseDragImg1 = False
                self.labelRoiItem.setPos((0,0))
                self.labelRoiItem.setSize((0,0))
                self.freeRoiItem.clear()
                return
            
            self.onEscape()
        elif isAltModifier:
            isCursorSizeAll = self.app.overrideCursor() == Qt.SizeAllCursor
            # Alt is pressed while cursor is on images --> set SizeAllCursor
            if self.xHoverImg is not None and not isCursorSizeAll:
                self.app.setOverrideCursor(Qt.SizeAllCursor)
        elif isCtrlModifier and isOverlaySegm:
            if ev.key() == Qt.Key_Up:
                val = self.imgGrad.labelsAlphaSlider.value()
                delta = 5/self.imgGrad.labelsAlphaSlider.maximum()
                val = val+delta
                self.imgGrad.labelsAlphaSlider.setValue(val, emitSignal=True)
            elif ev.key() == Qt.Key_Down:
                val = self.imgGrad.labelsAlphaSlider.value()
                delta = 5/self.imgGrad.labelsAlphaSlider.maximum()
                val = val-delta
                self.imgGrad.labelsAlphaSlider.setValue(val, emitSignal=True)
        elif ev.key() == Qt.Key_H:
            self.zoomToCells(enforce=True)
            if self.countKeyPress == 0:
                self.isKeyDoublePress = False
                self.countKeyPress = 1
                self.doubleKeyTimeElapsed = False
                self.Button = None
                QTimer.singleShot(400, self.doubleKeyTimerCallBack)
            elif self.countKeyPress == 1 and not self.doubleKeyTimeElapsed:
                self.ax1.autoRange()
                self.isKeyDoublePress = True
                self.countKeyPress = 0
        elif ev.key() == Qt.Key_Space:
            if self.countKeyPress == 0:
                # Single press --> wait that it's not double press
                self.isKeyDoublePress = False
                self.countKeyPress = 1
                self.doubleKeyTimeElapsed = False
                QTimer.singleShot(300, self.doubleKeySpacebarTimerCallback)
            elif self.countKeyPress == 1 and not self.doubleKeyTimeElapsed:
                self.isKeyDoublePress = True
                # Double press --> toggle draw nothing
                self.onDoubleSpaceBar()
                self.countKeyPress = 0
        elif isBrushKey or isEraserKey:
            mode = self.modeComboBox.currentText()
            if mode == 'Cell cycle analysis' or mode == 'Viewer':
                return
            if isBrushKey:
                self.Button = self.brushButton
            else:
                self.Button = self.eraserButton

            if self.countKeyPress == 0:
                # If first time clicking B activate brush and start timer
                # to catch double press of B
                if not self.Button.isChecked():
                    self.uncheck = False
                    self.Button.setChecked(True)
                else:
                    self.uncheck = True
                self.countKeyPress = 1
                self.isKeyDoublePress = False
                self.doubleKeyTimeElapsed = False

                QTimer.singleShot(400, self.doubleKeyTimerCallBack)
            elif self.countKeyPress == 1 and not self.doubleKeyTimeElapsed:
                self.isKeyDoublePress = True
                color = self.Button.palette().button().color().name()
                if color == self.doublePressKeyButtonColor:
                    c = self.defaultToolBarButtonColor
                else:
                    c = self.doublePressKeyButtonColor
                self.Button.setStyleSheet(f'background-color: {c}')
                self.countKeyPress = 0
                if self.xHoverImg is not None:
                    xdata, ydata = int(self.xHoverImg), int(self.yHoverImg)
                    if isBrushKey:
                        self.setHoverToolSymbolColor(
                            xdata, ydata, self.ax2_BrushCirclePen,
                            (self.ax2_BrushCircle, self.ax1_BrushCircle),
                            self.brushButton, brush=self.ax2_BrushCircleBrush
                        )
                    elif isEraserKey:
                        self.setHoverToolSymbolColor(
                            xdata, ydata, self.eraserCirclePen,
                            (self.ax2_EraserCircle, self.ax1_EraserCircle),
                            self.eraserButton
                        )

    def doubleKeyTimerCallBack(self):
        if self.isKeyDoublePress:
            self.doubleKeyTimeElapsed = False
            return
        self.doubleKeyTimeElapsed = True
        self.countKeyPress = 0
        if self.Button is None:
            return

        isBrushChecked = self.Button.isChecked()
        if isBrushChecked and self.uncheck:
            self.Button.setChecked(False)
        c = self.defaultToolBarButtonColor
        self.Button.setStyleSheet(f'background-color: {c}')

    def doubleKeySpacebarTimerCallback(self):
        if self.isKeyDoublePress:
            self.doubleKeyTimeElapsed = False
            return
        self.doubleKeyTimeElapsed = True
        self.countKeyPress = 0

        # # Spacebar single press --> toggle next visualization
        # currentIndex = self.drawIDsContComboBox.currentIndex()
        # nItems = self.drawIDsContComboBox.count()
        # nextIndex = currentIndex+1
        # if nextIndex < nItems:
        #     self.drawIDsContComboBox.setCurrentIndex(nextIndex)
        # else:
        #     self.drawIDsContComboBox.setCurrentIndex(0)

    def keyReleaseEvent(self, ev):
        if self.app.overrideCursor() == Qt.SizeAllCursor:
            self.app.restoreOverrideCursor()
        if ev.key() == Qt.Key_Control:
            self.isCtrlDown = False
        elif ev.key() == Qt.Key_Shift:
            if self.isSegm3D and self.xHoverImg is not None:
                # Restore normal brush cursor when releasing shift
                xdata, ydata = int(self.xHoverImg), int(self.yHoverImg)
                self.setHoverToolSymbolColor(
                    xdata, ydata, self.ax2_BrushCirclePen,
                    (self.ax2_BrushCircle, self.ax1_BrushCircle),
                    self.brushButton, brush=self.ax2_BrushCircleBrush
                )
                self.changeBrushID()
            self.isShiftDown = False
        canRepeat = (
            ev.key() == Qt.Key_Left
            or ev.key() == Qt.Key_Right
            or ev.key() == Qt.Key_Up
            or ev.key() == Qt.Key_Down
            or ev.key() == Qt.Key_Control
            or ev.key() == Qt.Key_Backspace
        )
        if canRepeat:
            return
        if ev.isAutoRepeat() and not ev.key() == Qt.Key_Z:
            msg = widgets.myMessageBox(showCentered=False, wrapText=False)
            txt = html_utils.paragraph(f"""
            Please, <b>do not keep the key "{ev.text().upper()}" 
            pressed.</b><br><br>
            It confuses me :)<br><br>
            Thanks!
            """)
            msg.warning(self, 'Release the key, please',txt)
        elif ev.isAutoRepeat() and ev.key() == Qt.Key_Z and self.isZmodifier:
            self.zKeptDown = True
        elif ev.key() == Qt.Key_Z and self.isZmodifier:
            self.isZmodifier = False
            if not self.zKeptDown:
                self.zSliceCheckbox.setChecked(not self.zSliceCheckbox.isChecked())
            self.zKeptDown = False

    def setUncheckedAllButtons(self):
        self.clickedOnBud = False
        try:
            self.BudMothTempLine.setData([], [])
        except Exception as e:
            pass
        for button in self.checkableButtons:
            button.setChecked(False)
        self.splineHoverON = False
        self.tempSegmentON = False
        self.isRightClickDragImg1 = False
        self.clearCurvItems(removeItems=False)
    
    def setUncheckedAllCustomAnnotButtons(self):
        for button in self.customAnnotDict.keys():
            button.setChecked(False)

    def propagateChange(
            self, modID, modTxt, doNotShow, UndoFutFrames,
            applyFutFrames, applyTrackingB=False, force=False
        ):
        """
        This function determines whether there are already visited future frames
        that contains "modID". If so, it triggers a pop-up asking the user
        what to do (propagate change to future frames o not)
        """
        posData = self.data[self.pos_i]
        # Do not check the future for the last frame
        if posData.frame_i+1 == posData.SizeT:
            # No future frames to propagate the change to
            return False, False, None, doNotShow

        includeUnvisited = posData.includeUnvisitedInfo.get(modTxt, False)
        areFutureIDs_affected = []
        # Get number of future frames already visited and check if future
        # frames has an ID affected by the change
        last_tracked_i_found = False
        segmSizeT = len(posData.segm_data)
        for i in range(posData.frame_i+1, segmSizeT):
            if posData.allData_li[i]['labels'] is None:
                if not last_tracked_i_found:
                    # We set last tracked frame at -1 first None found
                    last_tracked_i = i - 1
                    last_tracked_i_found = True
                if not includeUnvisited:
                    # Stop at last visited frame since includeUnvisited = False
                    break
                else:
                    lab = posData.segm_data[i]
            else:
                lab = posData.allData_li[i]['labels']
            
            if modID in lab:
                areFutureIDs_affected.append(True)
        
        if not last_tracked_i_found:
            # All frames have been visited in segm&track mode
            last_tracked_i = posData.SizeT - 1

        if last_tracked_i == posData.frame_i and not includeUnvisited:
            # No future frames to propagate the change to
            return False, False, None, doNotShow

        if not areFutureIDs_affected and not force:
            # There are future frames but they are not affected by the change
            return UndoFutFrames, False, None, doNotShow

        # Ask what to do unless the user has previously checked doNotShowAgain
        if doNotShow:
            endFrame_i = last_tracked_i
            return UndoFutFrames, applyFutFrames, endFrame_i, doNotShow
        else:
            addApplyAllButton = modTxt == 'Delete ID' or modTxt == 'Edit ID'
            ffa = apps.FutureFramesAction_QDialog(
                posData.frame_i+1, last_tracked_i, modTxt, 
                applyTrackingB=applyTrackingB, parent=self, 
                addApplyAllButton=addApplyAllButton
            )
            ffa.exec_()
            decision = ffa.decision

            if decision is None:
                return None, None, None, doNotShow

            endFrame_i = ffa.endFrame_i
            doNotShowAgain = ffa.doNotShowCheckbox.isChecked()

            self.onlyTracking = False
            if decision == 'apply_and_reinit':
                UndoFutFrames = True
                applyFutFrames = False
            elif decision == 'apply_and_NOTreinit':
                UndoFutFrames = False
                applyFutFrames = False
            elif decision == 'apply_to_all_visited':
                UndoFutFrames = False
                applyFutFrames = True
            elif decision == 'only_tracking':
                UndoFutFrames = False
                applyFutFrames = True
                self.onlyTracking = True
            elif decision == 'apply_to_all':
                UndoFutFrames = False
                applyFutFrames = True
                posData.includeUnvisitedInfo[modTxt] = True
        return UndoFutFrames, applyFutFrames, endFrame_i, doNotShowAgain

    def addCcaState(self, frame_i, cca_df, undoId):
        posData = self.data[self.pos_i]
        posData.UndoRedoCcaStates[frame_i].insert(
            0, {'id': undoId, 'cca_df': cca_df.copy()}
        )

    def addCurrentState(self, callbackOnDone=None, storeImage=False):
        posData = self.data[self.pos_i]
        if posData.cca_df is not None:
            cca_df = posData.cca_df.copy()
        else:
            cca_df = None

        if storeImage:
            image = self.img1.image.copy()
        else:
            image = None

        state = {
            'image': image,
            'labels': posData.lab.copy(),
            'editID_info': posData.editID_info.copy(),
            'binnedIDs': posData.binnedIDs.copy(),
            'keptObejctsIDs': self.keptObjectsIDs.copy(),
            'ripIDs': posData.ripIDs.copy(),
            'cca_df': cca_df
        }
        posData.UndoRedoStates[posData.frame_i].insert(0, state)
        
        # posData.storedLab = np.array(posData.lab, order='K', copy=True)
        # self.storeStateWorker.callbackOnDone = callbackOnDone
        # self.storeStateWorker.enqueue(posData, self.img1.image)

    def getCurrentState(self):
        posData = self.data[self.pos_i]
        i = posData.frame_i
        c = self.UndoCount
        state = posData.UndoRedoStates[i][c]
        if state['image'] is None:
            image_left = None
        else:
            image_left = state['image'].copy()
        posData.lab = state['labels'].copy()
        posData.editID_info = state['editID_info'].copy()
        posData.binnedIDs = state['binnedIDs'].copy()
        posData.ripIDs = state['ripIDs'].copy()
        self.keptObjectsIDs = state['keptObejctsIDs'].copy()
        cca_df = state['cca_df']
        if cca_df is not None:
            posData.cca_df = state['cca_df'].copy()
        else:
            posData.cca_df = None
        return image_left
    
    def storeLabelRoiParams(self, value=None, checked=True):
        checkedRoiType = self.labelRoiTypesGroup.checkedButton().text()
        circRoiRadius = self.labelRoiCircularRadiusSpinbox.value()
        roiZdepth = self.labelRoiZdepthSpinbox.value()
        autoClearBorder = self.labelRoiAutoClearBorderCheckbox.isChecked()
        clearBorder = 'Yes' if autoClearBorder else 'No'
        self.df_settings.at['labelRoi_checkedRoiType', 'value'] = checkedRoiType
        self.df_settings.at['labelRoi_circRoiRadius', 'value'] = circRoiRadius
        self.df_settings.at['labelRoi_roiZdepth', 'value'] = roiZdepth
        self.df_settings.at['labelRoi_autoClearBorder', 'value'] = clearBorder
        self.df_settings.at['labelRoi_replaceExistingObjects', 'value'] = (
            'Yes' if self.labelRoiReplaceExistingObjectsCheckbox.isChecked() 
            else 'No'
        )
        self.df_settings.to_csv(self.settings_csv_path)
    
    def loadLabelRoiLastParams(self):
        idx = 'labelRoi_checkedRoiType'
        if idx in self.df_settings.index:
            checkedRoiType = self.df_settings.at[idx, 'value']
            for button in self.labelRoiTypesGroup.buttons():
                if button.text() == checkedRoiType:
                    button.setChecked(True)
                    break
        
        idx = 'labelRoi_circRoiRadius'
        if idx in self.df_settings.index:
            circRoiRadius = self.df_settings.at[idx, 'value']
            self.labelRoiCircularRadiusSpinbox.setValue(int(circRoiRadius))
        
        idx = 'labelRoi_roiZdepth'
        if idx in self.df_settings.index:
            roiZdepth = self.df_settings.at[idx, 'value']
            self.labelRoiZdepthSpinbox.setValue(int(roiZdepth))
        
        idx = 'labelRoi_autoClearBorder'
        if idx in self.df_settings.index:
            clearBorder = self.df_settings.at[idx, 'value']
            checked = clearBorder == 'Yes'
            self.labelRoiAutoClearBorderCheckbox.setChecked(checked)
        
        idx = 'labelRoi_replaceExistingObjects'
        if idx in self.df_settings.index:
            val = self.df_settings.at[idx, 'value']
            checked = val == 'Yes'
            self.labelRoiReplaceExistingObjectsCheckbox.setChecked(checked)
        
        if self.labelRoiIsCircularRadioButton.isChecked():
            self.labelRoiCircularRadiusSpinbox.setDisabled(False)

    # @exec_time
    def storeUndoRedoStates(self, UndoFutFrames, storeImage=False):
        posData = self.data[self.pos_i]
        if UndoFutFrames:
            # Since we modified current frame all future frames that were already
            # visited are not valid anymore. Undo changes there
            self.reInitLastSegmFrame()
        
        # Keep only 5 Undo/Redo states
        if len(posData.UndoRedoStates[posData.frame_i]) > 5:
            posData.UndoRedoStates[posData.frame_i].pop(-1)

        # Restart count from the most recent state (index 0)
        # NOTE: index 0 is most recent state before doing last change
        self.UndoCount = 0
        self.undoAction.setEnabled(True)
        self.addCurrentState(storeImage=storeImage)
        
    def storeUndoRedoCca(self, frame_i, cca_df, undoId):
        if self.isSnapshot:
            # For snapshot mode we don't store anything because we have only
            # segmentation undo action active
            return
        """
        Store current cca_df along with a unique id to know which cca_df needs
        to be restored
        """

        posData = self.data[self.pos_i]

        # Restart count from the most recent state (index 0)
        # NOTE: index 0 is most recent state before doing last change
        self.UndoCcaCount = 0
        self.undoAction.setEnabled(True)

        self.addCcaState(frame_i, cca_df, undoId)

        # Keep only 10 Undo/Redo states
        if len(posData.UndoRedoCcaStates[frame_i]) > 10:
            posData.UndoRedoCcaStates[frame_i].pop(-1)

    def undoCustomAnnotation(self):
        pass

    def UndoCca(self):
        posData = self.data[self.pos_i]
        # Undo current ccaState
        storeState = False
        if self.UndoCount == 0:
            undoId = uuid.uuid4()
            self.addCcaState(posData.frame_i, posData.cca_df, undoId)
            storeState = True


        # Get previously stored state
        self.UndoCount += 1
        currentCcaStates = posData.UndoRedoCcaStates[posData.frame_i]
        prevCcaState = currentCcaStates[self.UndoCount]
        posData.cca_df = prevCcaState['cca_df']
        self.store_cca_df()
        self.updateAllImages()

        # Check if we have undone all states
        if len(currentCcaStates) > self.UndoCount:
            # There are no states left to undo for current frame_i
            self.undoAction.setEnabled(False)

        # Undo all past and future frames that has a last status inserted
        # when modyfing current frame
        prevStateId = prevCcaState['id']
        for frame_i in range(0, posData.SizeT):
            if storeState:
                cca_df_i = self.get_cca_df(frame_i=frame_i, return_df=True)
                if cca_df_i is None:
                    break
                # Store current state to enable redoing it
                self.addCcaState(frame_i, cca_df_i, undoId)

            CcaStates_i = posData.UndoRedoCcaStates[frame_i]
            if len(CcaStates_i) <= self.UndoCount:
                # There are no states to undo for frame_i
                continue

            CcaState_i = CcaStates_i[self.UndoCount]
            id_i = CcaState_i['id']
            if id_i != prevStateId:
                # The id of the state in frame_i is different from current frame
                continue

            cca_df_i = CcaState_i['cca_df']
            self.store_cca_df(frame_i=frame_i, cca_df=cca_df_i, autosave=False)
        
        self.enqAutosave()

    def undo(self):
        if self.UndoCount == 0:
            # Store current state to enable redoing it
            self.addCurrentState()
    
        posData = self.data[self.pos_i]
        # Get previously stored state
        if self.UndoCount < len(posData.UndoRedoStates[posData.frame_i])-1:
            self.UndoCount += 1
            # Since we have undone then it is possible to redo
            self.redoAction.setEnabled(True)

            # Restore state
            image_left = self.getCurrentState()
            self.update_rp()
            self.updateAllImages(image=image_left)
            self.store_data()

        if not self.UndoCount < len(posData.UndoRedoStates[posData.frame_i])-1:
            # We have undone all available states
            self.undoAction.setEnabled(False)

    def redo(self):
        posData = self.data[self.pos_i]
        # Get previously stored state
        if self.UndoCount > 0:
            self.UndoCount -= 1
            # Since we have redone then it is possible to undo
            self.undoAction.setEnabled(True)

            # Restore state
            image_left = self.getCurrentState()
            self.update_rp()
            self.updateAllImages(image=image_left)
            self.store_data()

        if not self.UndoCount > 0:
            # We have redone all available states
            self.redoAction.setEnabled(False)

    def realTimeTrackingClicked(self, checked):
        # Event called ONLY if the user click on Disable tracking
        # NOT called if setChecked is called. This allows to keep track
        # of the user choice. This way user con enforce tracking
        # NOTE: I know two booleans doing the same thing is overkill
        # but the code is more readable when we actually need them

        posData = self.data[self.pos_i]
        isRealTimeTrackingDisabled = not checked

        # Turn off smart tracking
        self.enableSmartTrackAction.toggled.disconnect()
        self.enableSmartTrackAction.setChecked(False)
        if isRealTimeTrackingDisabled:
            self.UserEnforced_DisabledTracking = True
            self.UserEnforced_Tracking = False
        else:
            txt = html_utils.paragraph("""

            Do you want to keep <b>tracking always active</b> including on already 
            visited frames?<br><br>
            Note: To re-activate automatic handling of tracking go to<br> 
            <code>Edit --> Smart handling of enabling/disabling tracking</code>.

            """)
            msg = widgets.myMessageBox(showCentered=False, wrapText=False)
            yesButton, noButton = msg.question(
                self, 'Keep tracking always active?', txt, 
                buttonsTexts=('Yes', 'No')
            )
            if msg.clickedButton == yesButton:
                self.repeatTracking()
                self.UserEnforced_DisabledTracking = False
                self.UserEnforced_Tracking = True
            else:
                self.enableSmartTrackAction.setChecked(True)

    @exception_handler
    def repeatTrackingVideo(self):
        posData = self.data[self.pos_i]
        win = apps.selectTrackerGUI(
            posData.SizeT, currentFrameNo=posData.frame_i+1
        )
        win.exec_()
        if win.cancel:
            self.logger.info('Tracking aborted.')
            return

        trackerName = win.selectedItemsText[0]
        self.logger.info(f'Importing {trackerName} tracker...')
        self.tracker, self.track_params = myutils.import_tracker(
            posData, trackerName, qparent=self
        )
        if self.track_params is None:
            self.logger.info('Tracking aborted.')
            return
        if 'image_channel_name' in self.track_params:
            # Remove the channel name since it was already loaded in import_tracker
            del self.track_params['image_channel_name']
        
        start_n = win.startFrame
        stop_n = win.stopFrame

        last_tracked_i = self.get_last_tracked_i()
        if start_n-1 <= last_tracked_i and start_n>1:
            proceed = self.warnRepeatTrackingVideoWithAnnotations(
                last_tracked_i, start_n
            )
            if not proceed:
                self.logger.info('Tracking aborted.')
                return
            
            self.logger.info(f'Removing annotations from frame n. {start_n}.')
            self.remove_future_cca_df(start_n-1)

        video_to_track = posData.segm_data
        for frame_i in range(start_n-1, stop_n):
            data_dict = posData.allData_li[frame_i]
            lab = data_dict['labels']
            if lab is None:
                break

            video_to_track[frame_i] = lab
        video_to_track = video_to_track[start_n-1:stop_n]

        self.start_n = start_n
        self.stop_n = stop_n

        self.progressWin = apps.QDialogWorkerProgress(
            title='Tracking', parent=self,
            pbarDesc=f'Tracking from frame n. {start_n} to {stop_n}...'
        )
        self.progressWin.show(self.app)
        self.progressWin.mainPbar.setMaximum(stop_n-start_n)
        self.startTrackingWorker(posData, video_to_track)

    def repeatTracking(self):
        posData = self.data[self.pos_i]
        prev_lab = self.get_2Dlab(posData.lab).copy()
        self.tracking(enforce=True, DoManualEdit=False)
        if posData.editID_info:
            editIDinfo = [
                f'Replace ID {posData.lab[y,x]} with {newID}'
                for y, x, newID in posData.editID_info
            ]
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph(f"""
                You requested to repeat tracking but there are the following
                manually edited IDs:<br><br>
                {editIDinfo}<br><br>
                Do you want to keep these edits or ignore them?
            """)
            _, keepManualEditButton, _ = msg.question(
                self, 'Repeat tracking mode', txt, 
                buttonsTexts=('Keep manually edited IDs', 'Ignore')
            )
            if msg.cancel:
                return
            if msg.clickedButton == keepManualEditButton:
                allIDs = [obj.label for obj in posData.rp]
                lab2D = self.get_2Dlab(posData.lab)
                self.manuallyEditTracking(lab2D, allIDs)
                self.update_rp()
                self.setAllTextAnnotations()
                self.highlightLostNew()
                # self.checkIDsMultiContour()
            else:
                posData.editID_info = []
        if np.any(posData.lab != prev_lab):
            if self.isSnapshot:
                self.fixCcaDfAfterEdit('Repeat tracking')
                self.updateAllImages()
            else:
                self.warnEditingWithCca_df('Repeat tracking')
        else:
            self.updateAllImages()

    def updateGhostMaskOpacity(self, alpha_percentage=None):
        if alpha_percentage is None:
            alpha_percentage = (
                self.manualTrackingToolbar.ghostMaskOpacitySpinbox.value()
            )
        alpha = alpha_percentage/100
        self.ghostMaskItemLeft.setOpacity(alpha)
        self.ghostMaskItemRight.setOpacity(alpha)

    def addManualTrackingItems(self):
        self.ghostContourItemLeft.addToPlotItem(self.ax1)
        self.ghostContourItemRight.addToPlotItem(self.ax2)

        self.ghostMaskItemLeft.addToPlotItem(self.ax1)
        self.ghostMaskItemRight.addToPlotItem(self.ax2)

        Y, X = self.img1.image.shape[:2]
        self.ghostMaskItemLeft.initImage((Y, X))
        self.ghostMaskItemRight.initImage((Y, X))

        self.updateGhostMaskOpacity()
    
    def removeManualTrackingItems(self):
        self.ghostContourItemLeft.removeFromPlotItem()
        self.ghostContourItemRight.removeFromPlotItem()

        self.ghostMaskItemLeft.removeFromPlotItem()
        self.ghostMaskItemRight.removeFromPlotItem()
    
    def initGhostObject(self, ID=None):
        mode = self.modeComboBox.currentText()
        if mode != 'Segmentation and Tracking':
            self.ghostObject = None
            return
        
        if not self.manualTrackingButton.isChecked():
            self.ghostObject = None
            return
        
        if not self.manualTrackingToolbar.showGhostCheckbox.isChecked():
            self.ghostObject = None
            return
        
        if ID is None:
            ID = self.manualTrackingToolbar.spinboxID.value()
        
        posData = self.data[self.pos_i]
        if posData.frame_i == 0:
            self.ghostObject = None
            return
        
        prevFrameRp = posData.allData_li[posData.frame_i-1]['regionprops']
        if prevFrameRp is None:
            self.ghostObject = None
            return
        
        for obj in prevFrameRp:
            if obj.label != ID:
                continue
            self.ghostObject = obj
            break
        else:
            self.ghostObject = None
            self.manualTrackingToolbar.showWarning(
                f'The ID {ID} does not exist in previous frame '
                '--> starting a new track.'
            )
            return
        
        self.manualTrackingToolbar.clearInfoText()

        self.ghostObject.contour = self.getObjContours(
            self.ghostObject, local=True
        )
        self.ghostObject.xx_contour = self.ghostObject.contour[:,1]
        self.ghostObject.yy_contour = self.ghostObject.contour[:,0]

        self.ghostMaskItemLeft.initLookupTable(self.lut[ID])
        self.ghostMaskItemRight.initLookupTable(self.lut[ID])
    
    def clearGhost(self):
        self.clearGhostContour()
        self.clearGhostMask()
    
    def clearGhostContour(self):
        self.ghostContourItemLeft.clear()
        self.ghostContourItemRight.clear()
    
    def clearGhostMask(self):
        self.ghostMaskItemLeft.clear()
        self.ghostMaskItemRight.clear()

    def manualTracking_cb(self, checked):
        self.manualTrackingToolbar.setVisible(checked)
        if checked:
            self.realTimeTrackingToggle.previousStatus = (
                self.realTimeTrackingToggle.isChecked()
            )
            self.realTimeTrackingToggle.setChecked(False)
            self.UserEnforced_DisabledTracking_previousStatus = (
                self.UserEnforced_DisabledTracking
            )
            self.UserEnforced_Tracking_previousStatus = (
                self.UserEnforced_Tracking
            )

            self.UserEnforced_DisabledTracking = True
            self.UserEnforced_Tracking = False
            self.initGhostObject()
            self.addManualTrackingItems()
        else:
            self.realTimeTrackingToggle.setChecked(
                self.realTimeTrackingToggle.previousStatus
            )
            self.UserEnforced_DisabledTracking = (
                self.UserEnforced_DisabledTracking_previousStatus
            )
            self.UserEnforced_Tracking = (
                self.UserEnforced_Tracking_previousStatus
            )
            self.removeManualTrackingItems()
            self.clearGhost()

    def autoSegm_cb(self, checked):
        if checked:
            self.askSegmParam = True
            # Ask which model
            models = myutils.get_list_of_models()
            win = widgets.QDialogListbox(
                'Select model',
                'Select model to use for segmentation: ',
                models,
                multiSelection=False,
                parent=self
            )
            win.exec_()
            if win.cancel:
                return
            model_name = win.selectedItemsText[0]
            self.segmModelName = model_name
            # Store undo state before modifying stuff
            self.storeUndoRedoStates(False)
            self.updateAllImages()
            self.computeSegm()
            self.askSegmParam = False
        else:
            self.segmModelName = None

    def randomWalkerSegm(self):
        # self.RWbkgrScatterItem = pg.ScatterPlotItem(
        #     symbol='o', size=2,
        #     brush=self.RWbkgrBrush,
        #     pen=self.RWbkgrPen
        # )
        # self.ax1.addItem(self.RWbkgrScatterItem)
        #
        # self.RWforegrScatterItem = pg.ScatterPlotItem(
        #     symbol='o', size=2,
        #     brush=self.RWforegrBrush,
        #     pen=self.RWforegrPen
        # )
        # self.ax1.addItem(self.RWforegrScatterItem)

        # Store undo state before modifying stuff
        self.storeUndoRedoStates(False)

        self.segmModelName = 'randomWalker'
        self.randomWalkerWin = apps.randomWalkerDialog(self)
        self.randomWalkerWin.setFont(_font)
        self.randomWalkerWin.show()
        self.randomWalkerWin.setSize()

    def postProcessSegm(self, checked):
        if self.isSegm3D:
            SizeZ = max([posData.SizeZ for posData in self.data])
        else:
            SizeZ = None
        if checked:
            self.postProcessSegmWin = apps.postProcessSegmDialog(
                SizeZ=SizeZ, mainWin=self
            )
            self.postProcessSegmWin.sigClosed.connect(
                self.postProcessSegmWinClosed
            )
            self.postProcessSegmWin.sigValueChanged.connect(
                self.postProcessSegmValueChanged
            )
            self.postProcessSegmWin.sigEditingFinished.connect(
                self.postProcessSegmEditingFinished
            )
            self.postProcessSegmWin.sigApplyToAllFutureFrames.connect(
                self.postProcessSegmApplyToAllFutureFrames
            )
            self.postProcessSegmWin.show()
            self.postProcessSegmWin.valueChanged(None)
        else:
            self.postProcessSegmWin.close()
            self.postProcessSegmWin = None
    
    def postProcessSegmApplyToAllFutureFrames(self, postProcessKwargs):
        proceed = self.warnEditingWithCca_df('post-processing segmentation')
        if not proceed:
            self.logger.info('Post-processing segmentation cancelled.')
            return

        self.progressWin = apps.QDialogWorkerProgress(
            title='Post-processing segmentation', parent=self,
            pbarDesc=f'Post-processing segmentation masks...'
        )
        self.progressWin.show(self.app)
        self.progressWin.mainPbar.setMaximum(0)

        self.startPostProcessSegmWorker(postProcessKwargs)
    
    def postProcessSegmEditingFinished(self):
        self.update_rp()
        self.store_data()
        self.updateAllImages()
    
    def postProcessSegmWorkerFinished(self):
        self.progressWin.workerFinished = True
        self.progressWin.close()
        self.progressWin = None
        self.get_data()
        self.updateAllImages()
        self.titleLabel.setText('Post-processing segmentation done!', color='w')
        self.logger.info('Post-processing segmentation done!')

    def postProcessSegmWinClosed(self):
        self.postProcessSegmWin = None
        self.postProcessSegmAction.toggled.disconnect()
        self.postProcessSegmAction.setChecked(False)
        self.postProcessSegmAction.toggled.connect(self.postProcessSegm)
    
    def postProcessSegmValueChanged(self, lab, delObjs: dict):
        for delObj in delObjs.values():
            self.clearObjContour(obj=delObj, ax=0)
            self.clearObjContour(obj=delObj, ax=1)
            
        posData = self.data[self.pos_i]
        
        labelsToSkip = {}
        for ID in posData.IDs:
            if ID in delObjs:
                labelsToSkip[ID] = True
                continue
            
            restoreObj = self.postProcessSegmWin.origObjs[ID]
            self.addObjContourToContoursImage(obj=restoreObj, ax=0)
            self.addObjContourToContoursImage(obj=restoreObj, ax=1)
 
        # self.setAllTextAnnotations(labelsToSkip=labelsToSkip)

        posData.lab = lab
        self.setImageImg2()
        if self.annotSegmMasksCheckbox.isChecked():
            self.labelsLayerImg1.setImage(self.currentLab2D, autoLevels=False)
        if self.annotSegmMasksCheckboxRight.isChecked():
            self.labelsLayerRightImg.setImage(self.currentLab2D, autoLevels=False)

    def readSavedCustomAnnot(self):
        tempAnnot = {}
        if os.path.exists(custom_annot_path):
            self.logger.info('Loading saved custom annotations...')
            tempAnnot = load.read_json(
                custom_annot_path, logger_func=self.logger.info
            )

        posData = self.data[self.pos_i]
        self.savedCustomAnnot = tempAnnot
        for pos_i, posData in enumerate(self.data):
            self.savedCustomAnnot = {
                **self.savedCustomAnnot, **posData.customAnnot
            }
    
    def addCustomAnnotButtonAllLoadedPos(self):
        allPosCustomAnnot = {}
        for pos_i, posData in enumerate(self.data):
            self.addCustomAnnotationSavedPos(pos_i=pos_i)
            allPosCustomAnnot = {**allPosCustomAnnot, **posData.customAnnot}
        for posData in self.data:
            posData.customAnnot = allPosCustomAnnot

    def addCustomAnnotationSavedPos(self, pos_i=None):
        if pos_i is None:
            pos_i = self.pos_i
        
        posData = self.data[pos_i]
        for name, annotState in posData.customAnnot.items():
            # Check if button is already present and update only annotated IDs
            buttons = [b for b in self.customAnnotDict.keys() if b.name==name]
            if buttons:
                toolButton = buttons[0]
                allAnnotedIDs = self.customAnnotDict[toolButton]['annotatedIDs']
                allAnnotedIDs[pos_i] = posData.customAnnotIDs.get(name, {})
                continue

            try:
                symbol = re.findall(r"\'(.+)\'", annotState['symbol'])[0]
            except Exception as e:
                self.logger.info(traceback.format_exc())
                symbol = 'o'
            
            symbolColor = QColor(*annotState['symbolColor'])
            shortcut = annotState['shortcut']
            if shortcut is not None:
                keySequence = widgets.macShortcutToWindows(shortcut)
                keySequence = QKeySequence(keySequence)
            else:
                keySequence = None
            toolTip = myutils.getCustomAnnotTooltip(annotState)
            keepActive = annotState.get('keepActive', True)
            isHideChecked = annotState.get('isHideChecked', True)

            toolButton, action = self.addCustomAnnotationButton(
                symbol, symbolColor, keySequence, toolTip, name,
                keepActive, isHideChecked
            )
            allPosAnnotIDs = [
                pos.customAnnotIDs.get(name, {}) for pos in self.data
            ]
            self.customAnnotDict[toolButton] = {
                'action': action,
                'state': annotState,
                'annotatedIDs': allPosAnnotIDs
            }

            self.addCustomAnnnotScatterPlot(symbolColor, symbol, toolButton)

    def addCustomAnnotationButton(
            self, symbol, symbolColor, keySequence, toolTip, annotName,
            keepActive, isHideChecked
        ):
        toolButton = widgets.customAnnotToolButton(
            symbol, symbolColor, parent=self, keepToolActive=keepActive,
            isHideChecked=isHideChecked
        )
        toolButton.setCheckable(True)
        self.checkableQButtonsGroup.addButton(toolButton)
        if keySequence is not None:
            toolButton.setShortcut(keySequence)
        toolButton.setToolTip(toolTip)
        toolButton.name = annotName
        toolButton.toggled.connect(self.customAnnotButtonToggled)
        toolButton.sigRemoveAction.connect(self.removeCustomAnnotButton)
        toolButton.sigKeepActiveAction.connect(self.customAnnotKeepActive)
        toolButton.sigHideAction.connect(self.customAnnotHide)
        toolButton.sigModifyAction.connect(self.customAnnotModify)
        action = self.annotateToolbar.addWidget(toolButton)
        return toolButton, action

    def addCustomAnnnotScatterPlot(
            self, symbolColor, symbol, toolButton
        ):
        # Add scatter plot item
        symbolColorBrush = [0, 0, 0, 50]
        symbolColorBrush[:3] = symbolColor.getRgb()[:3]
        scatterPlotItem = widgets.CustomAnnotationScatterPlotItem()
        scatterPlotItem.setData(
            [], [], symbol=symbol, pxMode=False,
            brush=pg.mkBrush(symbolColorBrush), size=15,
            pen=pg.mkPen(width=3, color=symbolColor),
            hoverable=True, hoverBrush=pg.mkBrush(symbolColor),
            tip=None
        )
        scatterPlotItem.sigHovered.connect(self.customAnnotHovered)
        scatterPlotItem.button = toolButton
        self.customAnnotDict[toolButton]['scatterPlotItem'] = scatterPlotItem
        self.ax1.addItem(scatterPlotItem)
    
    def addCustomAnnotationItems(
            self, symbol, symbolColor, keySequence, toolTip, name,
            keepActive, isHideChecked, state
        ):
        toolButton, action = self.addCustomAnnotationButton(
            symbol, symbolColor, keySequence, toolTip, name,
            keepActive, isHideChecked
        )

        self.customAnnotDict[toolButton] = {
            'action': action,
            'state': state,
            'annotatedIDs': [{} for _ in range(len(self.data))]
        }

        # Save custom annotation to cellacdc/temp/custom_annotations.json
        state_to_save = state.copy()
        state_to_save['symbolColor'] = tuple(symbolColor.getRgb())
        self.savedCustomAnnot[name] = state_to_save
        for posData in self.data:
            posData.customAnnot[name] = state_to_save

        # Add scatter plot item
        self.addCustomAnnnotScatterPlot(symbolColor, symbol, toolButton)

        # Add 0s column to acdc_df
        posData = self.data[self.pos_i]
        for frame_i, data_dict in enumerate(posData.allData_li):
            acdc_df = data_dict['acdc_df']
            if acdc_df is None:
                continue
            acdc_df[name] = 0
        if posData.acdc_df is not None:
            posData.acdc_df[name] = 0
        

    def customAnnotHovered(self, scatterPlotItem, points, event):
        # Show tool tip when hovering an annotation with annotation name and ID
        vb = scatterPlotItem.getViewBox()
        if vb is None:
            return
        if len(points) > 0:
            posData = self.data[self.pos_i]
            point = points[0]
            x, y = point.pos().x(), point.pos().y()
            xdata, ydata = int(x), int(y)
            ID = self.get_2Dlab(posData.lab)[ydata, xdata]
            vb.setToolTip(
                f'Annotation name: {scatterPlotItem.button.name}\n'
                f'ID = {ID}'
            )
        else:
            vb.setToolTip('')
    
    def loadCustomAnnotations(self):
        items = list(self.savedCustomAnnot.keys())
        if len(items) == 0:
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph("""
            There are no custom annotations saved.<br><br>
            Click on "Add custom annotation" button to start adding new 
            annotations.
            """)
            msg.warning(self, 'No annotations saved', txt)
            return
        
        self.selectAnnotWin = widgets.QDialogListbox(
            'Load previously used custom annotation(s)',
            'Select annotations to load:', items,
            additionalButtons=('Delete selected annnotations', ),
            parent=self, multiSelection=True
        )
        for button in self.selectAnnotWin._additionalButtons:
            button.disconnect()
            button.clicked.connect(self.deleteSavedAnnotation)
        self.selectAnnotWin.exec_()
        if self.selectAnnotWin.cancel:
            return
        
        for selectedAnnotName in self.selectAnnotWin.selectedItemsText:
            selectedAnnot = self.savedCustomAnnot[selectedAnnotName]

            symbol = selectedAnnot['symbol']
            symbol = re.findall(r"\'(.+)\'", symbol)[0]
            symbolColor = selectedAnnot['symbolColor']
            symbolColor = pg.mkColor(symbolColor)
            keySequence = QKeySequence(selectedAnnot['shortcut'])
            Type = selectedAnnot['type']
            toolTip = (
                f'Name: {selectedAnnotName}\n\n'
                f'Type: {Type}\n\n'
                f'Usage: activate the button and RIGHT-CLICK on cell to annotate\n\n'
                f'Description: {selectedAnnot["description"]}\n\n'
                f'SHORTCUT: "{keySequence}"'
            )
            keepActive = selectedAnnot['keepActive']
            isHideChecked = selectedAnnot['isHideChecked']
            state = {
                'type': Type,
                'name': selectedAnnotName,
                'symbol':  selectedAnnot['symbol'],
                'shortcut': selectedAnnot['shortcut'],
                'description': selectedAnnot["description"],
                'keepActive': keepActive,
                'isHideChecked': isHideChecked,
                'symbolColor': symbolColor
            }
            self.addCustomAnnotationItems(
                symbol, symbolColor, keySequence, toolTip, selectedAnnotName,
                keepActive, isHideChecked, state
            )
            for pos_i, posData in enumerate(self.data):
                posData.customAnnot[selectedAnnotName] = selectedAnnot
            
        self.saveCustomAnnot()
    
    def deleteSavedAnnotation(self):
        for item in self.selectAnnotWin.listBox.selectedItems():
            name = item.text()
            self.savedCustomAnnot.pop(name)
        self.deleteSelectedAnnot(self.selectAnnotWin.listBox.selectedItems())
        items = list(self.savedCustomAnnot.keys())
        self.selectAnnotWin.listBox.clear()
        self.selectAnnotWin.listBox.addItems(items)

    def addCustomAnnotation(self):
        self.readSavedCustomAnnot()

        self.addAnnotWin = apps.customAnnotationDialog(
            self.savedCustomAnnot, parent=self
        )
        self.addAnnotWin.sigDeleteSelecAnnot.connect(self.deleteSelectedAnnot)
        self.addAnnotWin.exec_()
        if self.addAnnotWin.cancel:
            return

        symbol = self.addAnnotWin.symbol
        symbolColor = self.addAnnotWin.state['symbolColor']
        keySequence = self.addAnnotWin.shortcutWidget.widget.keySequence
        toolTip = self.addAnnotWin.toolTip
        name = self.addAnnotWin.state['name']
        keepActive = self.addAnnotWin.state.get('keepActive', True)
        isHideChecked = self.addAnnotWin.state.get('isHideChecked', True)

        self.addCustomAnnotationItems(
            symbol, symbolColor, keySequence, toolTip, name,
            keepActive, isHideChecked, self.addAnnotWin.state
        )
        self.saveCustomAnnot()

    def viewAllCustomAnnot(self, checked):
        if not checked:
            # Clear all annotations before showing only checked
            for button in self.customAnnotDict.keys():
                self.clearScatterPlotCustomAnnotButton(button)
        self.doCustomAnnotation(0)

    def clearScatterPlotCustomAnnotButton(self, button):
        scatterPlotItem = self.customAnnotDict[button]['scatterPlotItem']
        scatterPlotItem.setData([], [])

    def saveCustomAnnot(self, only_temp=False):
        if not hasattr(self, 'savedCustomAnnot'):
            return

        if not self.savedCustomAnnot:
            return

        self.logger.info('Saving custom annotations parameters...')
        # Save to cell acdc temp path
        with open(custom_annot_path, mode='w') as file:
            json.dump(self.savedCustomAnnot, file, indent=2)

        if only_temp:
            return

        # Save to pos path
        for posData in self.data:
            with open(posData.custom_annot_json_path, mode='w') as file:
                json.dump(posData.customAnnot, file, indent=2)

    def customAnnotKeepActive(self, button):
        self.customAnnotDict[button]['state']['keepActive'] = button.keepToolActive

    def customAnnotHide(self, button):
        self.customAnnotDict[button]['state']['isHideChecked'] = button.isHideChecked
        clearAnnot = (
            not button.isChecked() and button.isHideChecked
            and not self.viewAllCustomAnnotAction.isChecked()
        )
        if clearAnnot:
            # User checked hide annot with the button not active --> clear
            self.clearScatterPlotCustomAnnotButton(button)
        elif not button.isChecked():
            # User uncheked hide annot with the button not active --> show
            self.doCustomAnnotation(0)

    def deleteSelectedAnnot(self, itemsToDelete):
        self.saveCustomAnnot(only_temp=True)

    def customAnnotModify(self, button):
        state = self.customAnnotDict[button]['state']
        self.addAnnotWin = apps.customAnnotationDialog(
            self.savedCustomAnnot, state=state
        )
        self.addAnnotWin.sigDeleteSelecAnnot.connect(self.deleteSelectedAnnot)
        self.addAnnotWin.exec_()
        if self.addAnnotWin.cancel:
            return

        # Rename column if existing
        posData = self.data[self.pos_i]
        acdc_df = posData.allData_li[posData.frame_i]['acdc_df']
        if acdc_df is not None:
            old_name = self.customAnnotDict[button]['state']['name']
            new_name = self.addAnnotWin.state['name']
            acdc_df = acdc_df.rename(columns={old_name: new_name})
            posData.allData_li[posData.frame_i]['acdc_df'] = acdc_df

        self.customAnnotDict[button]['state'] = self.addAnnotWin.state

        name = self.addAnnotWin.state['name']
        state_to_save = self.addAnnotWin.state.copy()
        symbolColor = self.addAnnotWin.state['symbolColor']
        state_to_save['symbolColor'] = tuple(symbolColor.getRgb())
        self.savedCustomAnnot[name] = self.addAnnotWin.state
        self.saveCustomAnnot()

        symbol = self.addAnnotWin.symbol
        symbolColor = self.customAnnotDict[button]['state']['symbolColor']
        button.setColor(symbolColor)
        button.update()
        symbolColorBrush = [0, 0, 0, 50]
        symbolColorBrush[:3] = symbolColor.getRgb()[:3]
        scatterPlotItem = self.customAnnotDict[button]['scatterPlotItem']
        xx, yy = scatterPlotItem.getData()
        if xx is None:
            xx, yy = [], []
        scatterPlotItem.setData(
            xx, yy, symbol=symbol, pxMode=False,
            brush=pg.mkBrush(symbolColorBrush), size=15,
            pen=pg.mkPen(width=3, color=symbolColor)
        )

    def doCustomAnnotation(self, ID, fromClick=False):
        # NOTE: pass 0 for ID to not add
        posData = self.data[self.pos_i]
        if self.viewAllCustomAnnotAction.isChecked() and not fromClick:
            # User requested to show all annotations --> iterate all buttons
            # Unless it actively clicked to annotate --> avoid annotating object
            # with all the annotations present
            buttons = list(self.customAnnotDict.keys())
        else:
            # Annotate if the button is active or isHideChecked is False
            buttons = [
                b for b in self.customAnnotDict.keys()
                if (b.isChecked() or not b.isHideChecked)
            ]
            if not buttons:
                return

        for button in buttons:
            annotatedIDs = self.customAnnotDict[button]['annotatedIDs'][self.pos_i]
            annotIDs_frame_i = annotatedIDs.get(posData.frame_i, [])
            if ID in annotIDs_frame_i:
                annotIDs_frame_i.remove(ID)
            elif ID != 0:
                annotIDs_frame_i.append(ID)

            annotPerButton = self.customAnnotDict[button]
            allAnnotedIDs = annotPerButton['annotatedIDs']
            posAnnotedIDs = allAnnotedIDs[self.pos_i]
            posAnnotedIDs[posData.frame_i] = annotIDs_frame_i

            state = self.customAnnotDict[button]['state']
            acdc_df = posData.allData_li[posData.frame_i]['acdc_df']
            if acdc_df is None:
                # visiting new frame for single time-point annot type do nothing
                return

            acdc_df[state['name']] = 0

            xx, yy = [], []
            for annotID in annotIDs_frame_i:
                obj_idx = posData.IDs.index(annotID)
                obj = posData.rp[obj_idx]
                acdc_df.at[annotID, state['name']] = 1
                if not self.isObjVisible(obj.bbox):
                    continue
                y, x = self.getObjCentroid(obj.centroid)
                xx.append(x)
                yy.append(y)
                
            scatterPlotItem = self.customAnnotDict[button]['scatterPlotItem']
            scatterPlotItem.setData(xx, yy)

            posData.allData_li[posData.frame_i]['acdc_df'] = acdc_df
        
        if self.highlightedID != 0:
            self.highlightedID = 0
            self.highlightIDcheckBoxToggled(False)

        if buttons:
            return buttons[0]

    def removeCustomAnnotButton(self, button, askHow=True, save=True):
        if askHow:
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph("""
                Do you want to <b>remove also the column with annotations</b> or 
                only the annotation button?<br>
            """)
            _, removeOnlyButton, removeColButton = msg.question(
                self, 'Remove only button?', txt, 
                buttonsTexts=(
                    'Cancel', 'Remove only button', 
                    ' Remove also column with annotations '
                )
            )
            if msg.cancel:
                return
            removeOnlyButton = msg.clickedButton == removeOnlyButton
        else:
            removeOnlyButton = True
        
        name = self.customAnnotDict[button]['state']['name']
        # remove annotation from position
        for posData in self.data:
            try:
                posData.customAnnot.pop(name)
            except KeyError as e:
                # Current pos doesn't have any annotation button. Continue
                continue

            if posData.acdc_df is None:
                continue
            
            if removeOnlyButton:
                continue

            posData.acdc_df = posData.acdc_df.drop(
                columns=name, errors='ignore'
            )
            for frame_i, data_dict in enumerate(posData.allData_li):
                acdc_df = data_dict['acdc_df']
                if acdc_df is None:
                    continue
                acdc_df = acdc_df.drop(columns=name, errors='ignore')
                posData.allData_li[frame_i]['acdc_df'] = acdc_df

        self.clearScatterPlotCustomAnnotButton(button)

        action = self.customAnnotDict[button]['action']
        self.annotateToolbar.removeAction(action)
        self.checkableQButtonsGroup.removeButton(button)
        self.customAnnotDict.pop(button)
        # self.savedCustomAnnot.pop(name)

        self.saveCustomAnnot(only_temp=True)

    def customAnnotButtonToggled(self, checked):
        if checked:
            self.customAnnotButton = self.sender()
            # Uncheck the other buttons
            for button in self.customAnnotDict.keys():
                if button == self.sender():
                    continue

                button.toggled.disconnect()
                self.clearScatterPlotCustomAnnotButton(button)
                button.setChecked(False)                
                button.toggled.connect(self.customAnnotButtonToggled)
            self.doCustomAnnotation(0)
        else:
            self.customAnnotButton = None
            button = self.sender()
            clearAnnotation = (
                button.isHideChecked 
                or not self.viewAllCustomAnnotAction.isChecked()
            )
            if clearAnnotation:    
                self.clearScatterPlotCustomAnnotButton(button)
            self.highlightIDcheckBoxToggled(False)
            self.resetCursor()
    
    def resetCursor(self):
        if self.app.overrideCursor() is not None:
            while self.app.overrideCursor() is not None:
                self.app.restoreOverrideCursor()

    def segmFrameCallback(self, action):
        if action == self.addCustomModelFrameAction:
            return
        
        idx = self.segmActions.index(action)
        model_name = self.modelNames[idx]
        self.repeatSegm(model_name=model_name, askSegmParams=True)

    def segmVideoCallback(self, action):
        if action == self.addCustomModelVideoAction:
            return

        posData = self.data[self.pos_i]
        win = apps.startStopFramesDialog(
            posData.SizeT, currentFrameNum=posData.frame_i+1
        )
        win.exec_()
        if win.cancel:
            self.logger.info('Segmentation on multiple frames aborted.')
            return

        idx = self.segmActionsVideo.index(action)
        model_name = self.modelNames[idx]
        self.repeatSegmVideo(model_name, win.startFrame, win.stopFrame)
    
    def segmentToolActionTriggered(self):
        if self.segmModelName is None:
            win = apps.QDialogSelectModel(parent=self)
            win.exec_()
            if win.cancel:
                self.logger.info('Repeat segmentation cancelled.')
                return
            model_name = win.selectedModel
            self.repeatSegm(
                model_name=model_name, askSegmParams=True
            )
        else:
            self.repeatSegm(model_name=self.segmModelName)

    @exception_handler
    def repeatSegm(self, model_name='', askSegmParams=False, return_model=False):
        if model_name == 'thresholding':
            # thresholding model is stored as 'Automatic thresholding'
            # at line of code `models.append('Automatic thresholding')`
            model_name = 'Automatic thresholding'
        
        idx = self.modelNames.index(model_name)
        # Ask segm parameters if not already set
        # and not called by segmSingleFrameMenu (askSegmParams=False)
        if not askSegmParams:
            askSegmParams = self.model_kwargs is None

        self.downloadWin = apps.downloadModel(model_name, parent=self)
        self.downloadWin.download()

        # Store undo state before modifying stuff
        self.storeUndoRedoStates(False)

        if model_name == 'Automatic thresholding':
            # Automatic thresholding is the name of the models as stored 
            # in self.modelNames, but the actual model is called thresholding
            # (see cellacdc/models/thresholding)
            model_name = 'thresholding'

        posData = self.data[self.pos_i]
        # Check if model needs to be imported
        acdcSegment = self.acdcSegment_li[idx]
        if acdcSegment is None:
            self.logger.info(f'Importing {model_name}...')
            acdcSegment = myutils.import_segment_module(model_name)
            self.acdcSegment_li[idx] = acdcSegment

        # Ask parameters if the user clicked on the action
        # Otherwise this function is called by "computeSegm" function and
        # we use loaded parameters
        if askSegmParams:
            if self.app.overrideCursor() == Qt.WaitCursor:
                self.app.restoreOverrideCursor()
            self.segmModelName = model_name
            # Read all models parameters
            init_params, segment_params = myutils.getModelArgSpec(acdcSegment)
            # Prompt user to enter the model parameters
            try:
                url = acdcSegment.url_help()
            except AttributeError:
                url = None
            
            initLastParams = True
            if model_name == 'thresholding':
                win = apps.QDialogAutomaticThresholding(
                    parent=self, isSegm3D=self.isSegm3D
                )
                win.exec_()
                if win.cancel:
                    return
                self.model_kwargs = win.segment_kwargs
                thresh_method = self.model_kwargs['threshold_method']
                gauss_sigma = self.model_kwargs['gauss_sigma']
                segment_params = myutils.insertModelArgSpect(
                    segment_params, 'threshold_method', thresh_method
                )
                segment_params = myutils.insertModelArgSpect(
                    segment_params, 'gauss_sigma', gauss_sigma
                )
                initLastParams = False

            _SizeZ = None
            if self.isSegm3D:
                _SizeZ = posData.SizeZ
            
            segm_files = load.get_segm_files(posData.images_path)
            existingSegmEndnames = load.get_existing_segm_endnames(
                posData.basename, segm_files
            )
            win = apps.QDialogModelParams(
                init_params,
                segment_params,
                model_name, parent=self,
                url=url, initLastParams=initLastParams, SizeZ=_SizeZ,
                segmFileEndnames=existingSegmEndnames
            )
            win.setChannelNames(posData.chNames)
            win.exec_()
            if win.cancel:
                self.logger.info('Segmentation process cancelled.')
                self.titleLabel.setText('Segmentation process cancelled.')
                return

            if model_name != 'thresholding':
                self.model_kwargs = win.model_kwargs
            self.removeArtefactsKwargs = win.artefactsGroupBox.kwargs()
            self.applyPostProcessing = win.applyPostProcessing
            self.secondChannelName = win.secondChannelName

            use_gpu = win.init_kwargs.get('gpu', False)
            proceed = myutils.check_cuda(model_name, use_gpu, qparent=self)
            if not proceed:
                self.logger.info('Segmentation process cancelled.')
                self.titleLabel.setText('Segmentation process cancelled.')
                return
            
            model = myutils.init_segm_model(acdcSegment, posData, win.init_kwargs)            
            try:
                model.setupLogger(self.logger)
            except Exception as e:
                pass
            self.models[idx] = model

            postProcessParams = {
                'applied_postprocessing': self.applyPostProcessing
            }
            postProcessParams = {**postProcessParams, **self.removeArtefactsKwargs}
            posData.saveSegmHyperparams(
                model_name, self.model_kwargs, postProcessParams
            )
            model.model_name = model_name
        else:
            model = self.models[idx]
        
        if return_model:
            return model

        self.titleLabel.setText(
            f'Labelling with {model_name}... '
            '(check progress in terminal/console)', color=self.titleColor
        )

        if self.askRepeatSegment3D:
            self.segment3D = False
        if self.isSegm3D and self.askRepeatSegment3D:
            msg = widgets.myMessageBox(showCentered=False)
            msg.addDoNotShowAgainCheckbox(text='Do not ask again')
            txt = html_utils.paragraph(
                'Do you want to segment the <b>entire z-stack</b> or only the '
                '<b>current z-slice</b>?'
            )
            _, segment3DButton, _ = msg.question(
                self, '3D segmentation?', txt,
                buttonsTexts=('Cancel', 'Segment 3D z-stack', 'Segment 2D z-slice')
            )
            if msg.cancel:
                self.titleLabel.setText('Segmentation process aborted.')
                self.logger.info('Segmentation process aborted.')
                return
            self.segment3D = msg.clickedButton == segment3DButton
            if msg.doNotShowAgainCheckbox.isChecked():
                self.askRepeatSegment3D = False
        
        if self.askZrangeSegm3D:
            self.z_range = None
        if self.isSegm3D and self.segment3D and self.askZrangeSegm3D:
            idx = (posData.filename, posData.frame_i)
            try:
                orignal_z = posData.segmInfo_df.at[idx, 'z_slice_used_gui']
            except ValueError as e:
                orignal_z = posData.segmInfo_df.loc[idx, 'z_slice_used_gui'].iloc[0] 
            selectZtool = apps.QCropZtool(
                posData.SizeZ, parent=self, cropButtonText='Ok',
                addDoNotShowAgain=True, title='Select z-slice range to segment'
            )
            selectZtool.sigZvalueChanged.connect(self.selectZtoolZvalueChanged)
            selectZtool.sigCrop.connect(selectZtool.close)
            selectZtool.exec_()
            self.update_z_slice(orignal_z)
            if selectZtool.cancel:
                self.titleLabel.setText('Segmentation process aborted.')
                self.logger.info('Segmentation process aborted.')
                return
            startZ = selectZtool.lowerZscrollbar.value()
            stopZ = selectZtool.upperZscrollbar.value()
            self.z_range = (startZ, stopZ)
            if selectZtool.doNotShowAgainCheckbox.isChecked():
                self.askZrangeSegm3D = False
        
        secondChannelData = None
        if self.secondChannelName is not None:
            secondChannelData = self.getSecondChannelData()
        
        self.titleLabel.setText(
            f'{model_name} is thinking... '
            '(check progress in terminal/console)', color=self.titleColor
        )

        self.model = model

        self.thread = QThread()
        self.worker = workers.segmWorker(
            self, secondChannelData=secondChannelData
        )
        self.worker.z_range = self.z_range
        self.worker.moveToThread(self.thread)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        # Custom signals
        self.worker.critical.connect(self.workerCritical)
        self.worker.finished.connect(self.segmWorkerFinished)

        self.thread.started.connect(self.worker.run)
        self.thread.start()
    
    def selectZtoolZvalueChanged(self, whichZ, z):
        self.update_z_slice(z)

    @exception_handler
    def repeatSegmVideo(self, model_name, startFrameNum, stopFrameNum):
        if model_name == 'thresholding':
            # thresholding model is stored as 'Automatic thresholding'
            # at line of code `models.append('Automatic thresholding')`
            model_name = 'Automatic thresholding'

        idx = self.modelNames.index(model_name)

        self.downloadWin = apps.downloadModel(model_name, parent=self)
        self.downloadWin.download()

        if model_name == 'Automatic thresholding':
            # Automatic thresholding is the name of the models as stored 
            # in self.modelNames, but the actual model is called thresholding
            # (see cellacdc/models/thresholding)
            model_name = 'thresholding'

        posData = self.data[self.pos_i]
        # Check if model needs to be imported
        acdcSegment = self.acdcSegment_li[idx]
        if acdcSegment is None:
            self.logger.info(f'Importing {model_name}...')
            acdcSegment = myutils.import_segment_module(model_name)
            self.acdcSegment_li[idx] = acdcSegment

        # Read all models parameters
        init_params, segment_params = myutils.getModelArgSpec(acdcSegment)
        # Prompt user to enter the model parameters
        try:
            url = acdcSegment.url_help()
        except AttributeError:
            url = None
        
        if model_name == 'thresholding':
            autoThreshWin = apps.QDialogAutomaticThresholding(
                parent=self, isSegm3D=self.isSegm3D
            )
            autoThreshWin.exec_()
            if autoThreshWin.cancel:
                return
        
        _SizeZ = None
        if self.isSegm3D:
            _SizeZ = posData.SizeZ  
        
        segm_files = load.get_segm_files(posData.images_path)
        existingSegmEndnames = load.get_existing_segm_endnames(
            posData.basename, segm_files
        )
        win = apps.QDialogModelParams(
            init_params,
            segment_params,
            model_name, parent=self,
            url=url, SizeZ=_SizeZ,
            segmFileEndnames=existingSegmEndnames
        )
        win.setChannelNames(posData.chNames)
        win.exec_()
        if win.cancel:
            self.logger.info('Segmentation process cancelled.')
            self.titleLabel.setText('Segmentation process cancelled.')
            return
        
        if model_name == 'thresholding':
            win.model_kwargs = autoThreshWin.segment_kwargs

        secondChannelData = None
        if win.secondChannelName is not None:
            secondChannelData = self.getSecondChannelData()

        use_gpu = win.init_kwargs.get('gpu', False)
        proceed = myutils.check_cuda(model_name, use_gpu, qparent=self)
        if not proceed:
            self.logger.info('Segmentation process cancelled.')
            self.titleLabel.setText('Segmentation process cancelled.')
            return

        model = myutils.init_segm_model(acdcSegment, posData, win.init_kwargs) 
        try:
            model.setupLogger(self.logger)
        except Exception as e:
            pass

        self.reInitLastSegmFrame(from_frame_i=startFrameNum-1)

        self.titleLabel.setText(
            f'{model_name} is thinking... '
            '(check progress in terminal/console)', color=self.titleColor
        )

        self.progressWin = apps.QDialogWorkerProgress(
            title='Segmenting video', parent=self,
            pbarDesc=f'Segmenting from frame n. {startFrameNum} to {stopFrameNum}...'
        )
        self.progressWin.show(self.app)
        self.progressWin.mainPbar.setMaximum(stopFrameNum-startFrameNum)

        self.thread = QThread()
        self.worker = workers.segmVideoWorker(
            posData, win, model, startFrameNum, stopFrameNum
        )
        self.worker.secondChannelData = secondChannelData
        self.worker.moveToThread(self.thread)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        # Custom signals
        self.worker.critical.connect(self.workerCritical)
        self.worker.finished.connect(self.segmVideoWorkerFinished)
        self.worker.progressBar.connect(self.workerUpdateProgressbar)

        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def segmVideoWorkerFinished(self, exec_time):
        self.progressWin.workerFinished = True
        self.progressWin.close()
        self.progressWin = None

        posData = self.data[self.pos_i]

        self.get_data()
        self.tracking(enforce=True)
        self.updateAllImages()

        txt = f'Done. Segmentation computed in {exec_time:.3f} s'
        self.logger.info('-----------------')
        self.logger.info(txt)
        self.logger.info('=================')
        self.titleLabel.setText(txt, color='g')

    @exception_handler
    def lazyLoaderCritical(self, error):
        if self.progressWin is not None:
            self.progressWin.workerFinished = True
            self.progressWin.close()
            self.lazyLoader.pause()
        raise error
    
    @exception_handler
    def workerCritical(self, error):
        if self.progressWin is not None:
            self.progressWin.workerFinished = True
            self.progressWin.close()
        raise error
    
    def saveDataWorkerCritical(self, error):
        self.logger.info(
            f'[WARNING]: Saving process stopped because of critical error.'
        )
        self.saveWin.aborted = True
        self.worker.finished.emit()
        self.workerCritical(error)
    
    def lazyLoaderWorkerClosed(self):
        if self.lazyLoader.salute:
            print('Cell-ACDC GUI closed.')     
            self.sigClosed.emit(self)
        
        self.lazyLoader = None

    def debugSegmWorker(self, lab):
        apps.imshow_tk(lab)

    def segmWorkerFinished(self, lab, exec_time):
        posData = self.data[self.pos_i]

        if posData.segmInfo_df is not None and posData.SizeZ>1:
            idx = (posData.filename, posData.frame_i)
            posData.segmInfo_df.at[idx, 'resegmented_in_gui'] = True

        if lab.ndim == 2 and self.isSegm3D:
            self.set_2Dlab(lab)
        else:
            posData.lab = lab.copy()

        self.update_rp()
        self.tracking(enforce=True)
        
        if self.isSnapshot:
            self.fixCcaDfAfterEdit('Repeat segmentation')
            self.updateAllImages()
        else:
            self.warnEditingWithCca_df('Repeat segmentation')

        self.ax1.autoRange()

        txt = f'Done. Segmentation computed in {exec_time:.3f} s'
        self.logger.info('-----------------')
        self.logger.info(txt)
        self.logger.info('=================')
        self.titleLabel.setText(txt, color='g')
        self.checkIfAutoSegm()

    # @exec_time
    def getDisplayedImg1(self):
        return self.img1.image
    
    def getDisplayedZstack(self):
        filteredData = self.filteredData.get(self.user_ch_name)
        if filteredData is None:
            posData = self.data[self.pos_i]
            return posData.img_data[posData.frame_i]
        else:
            return filteredData

    def autoAssignBud_YeastMate(self):
        if not self.is_win:
            txt = (
                'YeastMate is available only on Windows OS.'
                'We are working on expading support also on macOS and Linux.\n\n'
                'Thank you for your patience!'
            )
            msg = QMessageBox()
            msg.critical(
                self, 'Supported only on Windows', txt, msg.Ok
            )
            return


        model_name = 'YeastMate'
        idx = self.modelNames.index(model_name)

        self.titleLabel.setText(
            f'{model_name} is thinking... '
            '(check progress in terminal/console)', color=self.titleColor
        )

        # Store undo state before modifying stuff
        self.storeUndoRedoStates(False)

        posData = self.data[self.pos_i]
        # Check if model needs to be imported
        acdcSegment = self.acdcSegment_li[idx]
        if acdcSegment is None:
            acdcSegment = myutils.import_segment_module(model_name)
            self.acdcSegment_li[idx] = acdcSegment

        # Read all models parameters
        init_params, segment_params = myutils.getModelArgSpec(acdcSegment)
        # Prompt user to enter the model parameters
        try:
            url = acdcSegment.url_help()
        except AttributeError:
            url = None

        _SizeZ = None
        if self.isSegm3D:
            _SizeZ = posData.SizeZ 
        win = apps.QDialogModelParams(
            init_params,
            segment_params,
            model_name, url=url, SizeZ=_SizeZ
        )
        win.exec_()
        if win.cancel:
            self.titleLabel.setText('Segmentation aborted.')
            return

        use_gpu = win.init_kwargs.get('gpu', False)
        proceed = myutils.check_cuda(model_name, use_gpu, qparent=self)
        if not proceed:
            self.logger.info('Segmentation process cancelled.')
            self.titleLabel.setText('Segmentation process cancelled.')
            return
            
        self.model_kwargs = win.model_kwargs
        model = myutils.init_segm_model(acdcSegment, posData, win.init_kwargs) 
        try:
            model.setupLogger(self.logger)
        except Exception as e:
            pass

        self.models[idx] = model

        img = self.getDisplayedImg1()

        posData.cca_df = model.predictCcaState(img, posData.lab)
        self.store_data()
        self.updateAllImages()

        self.titleLabel.setText('Budding event prediction done.', color='g')
    
    def nextActionTriggered(self):
        stepAddAction = QAbstractSlider.SliderAction.SliderSingleStepAdd
        if self.zKeptDown or self.zSliceCheckbox.isChecked():
            self.zSliceScrollBar.triggerAction(stepAddAction)
        else:
            self.navigateScrollBar.triggerAction(stepAddAction)
    
    def prevActionTriggered(self):
        stepSubAction = QAbstractSlider.SliderAction.SliderSingleStepSub
        if self.zKeptDown or self.zSliceCheckbox.isChecked():
            self.zSliceScrollBar.triggerAction(stepSubAction)
        else:
            self.navigateScrollBar.triggerAction(stepSubAction)

    @exception_handler
    def next_cb(self):
        if self.isSnapshot:
            self.next_pos()
        else:
            self.next_frame()
        if self.curvToolButton.isChecked():
            self.curvTool_cb(True)
        
        self.updatePropsWidget('')

    @exception_handler
    def prev_cb(self):
        if self.isSnapshot:
            self.prev_pos()
        else:
            self.prev_frame()
        if self.curvToolButton.isChecked():
            self.curvTool_cb(True)
        
        self.updatePropsWidget('')

    def zoomOut(self):
        self.ax1.autoRange()

    def zoomToObjsActionCallback(self):
        self.zoomToCells(enforce=True)

    def zoomToCells(self, enforce=False):
        if not self.enableAutoZoomToCellsAction.isChecked() and not enforce:
            return

        posData = self.data[self.pos_i]
        lab_mask = (self.currentLab2D>0).astype(np.uint8)
        rp = skimage.measure.regionprops(lab_mask)
        if not rp:
            Y, X = lab_mask.shape
            xRange = -0.5, X+0.5
            yRange = -0.5, Y+0.5
        else:
            obj = rp[0]
            min_row, min_col, max_row, max_col = self.getObjBbox(obj.bbox)
            xRange = min_col-10, max_col+10
            yRange = max_row+10, min_row-10

        self.ax1.setRange(xRange=xRange, yRange=yRange)

    def viewCcaTable(self):
        posData = self.data[self.pos_i]
        self.logger.info('========================')
        self.logger.info('CURRENT Cell cycle analysis table:')
        self.logger.info(posData.cca_df)
        self.logger.info('------------------------')
        self.logger.info(f'STORED Cell cycle analysis table for frame {posData.frame_i+1}:')
        df = posData.allData_li[posData.frame_i]['acdc_df']
        if 'cell_cycle_stage' in df.columns:
            cca_df = df[self.cca_df_colnames]
            self.logger.info(cca_df)
            cca_df = cca_df.merge(
                posData.cca_df, how='outer', left_index=True, right_index=True,
                suffixes=('_STORED', '_CURRENT')
            )
            cca_df = cca_df.reindex(sorted(cca_df.columns), axis=1)
            num_cols = len(cca_df.columns)
            for j in range(0,num_cols,2):
                df_j_x = cca_df.iloc[:,j]
                df_j_y = cca_df.iloc[:,j+1]
                if any(df_j_x!=df_j_y):
                    self.logger.info('------------------------')
                    self.logger.info('DIFFERENCES:')
                    diff_df = cca_df.iloc[:,j:j+2]
                    diff_mask = diff_df.iloc[:,0]!=diff_df.iloc[:,1]
                    self.logger.info(diff_df[diff_mask])
        else:
            cca_df = None
            self.logger.info(cca_df)
        self.logger.info('========================')
        if posData.cca_df is None:
            return
        if posData.cca_df.empty:
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph(
                'Cell cycle annotations\' table is <b>empty</b>.<br>'
            )
            msg.warning(self, 'Table empty', txt)
            return
        
        df = posData.add_tree_cols_to_cca_df(
            posData.cca_df, frame_i=posData.frame_i
        )
        if self.ccaTableWin is None:
            self.ccaTableWin = apps.pdDataFrameWidget(df, parent=self)
            self.ccaTableWin.show()
            self.ccaTableWin.setGeometryWindow()
        else:
            self.ccaTableWin.setFocus()
            self.ccaTableWin.activateWindow()
            self.ccaTableWin.updateTable(posData.cca_df)

    def updateScrollbars(self):
        self.updateItemsMousePos()
        self.updateFramePosLabel()
        posData = self.data[self.pos_i]
        pos = self.pos_i+1 if self.isSnapshot else posData.frame_i+1
        self.navigateScrollBar.setSliderPosition(pos)
        if posData.SizeZ > 1:
            self.updateZsliceScrollbar(posData.frame_i)
            idx = (posData.filename, posData.frame_i)
            try:
                how = posData.segmInfo_df.at[idx, 'which_z_proj_gui']
            except ValueError as e:
                how = posData.segmInfo_df.loc[idx, 'which_z_proj_gui'].iloc[0] 
            self.zProjComboBox.setCurrentText(how)
            self.zSliceScrollBar.setMaximum(posData.SizeZ-1)
            self.zSliceSpinbox.setMaximum(posData.SizeZ)
            self.SizeZlabel.setText(f'/{posData.SizeZ}')

    def updateItemsMousePos(self):
        if self.brushButton.isChecked():
            self.updateBrushCursor(self.xHoverImg, self.yHoverImg)

        if self.eraserButton.isChecked():
            self.updateEraserCursor(self.xHoverImg, self.yHoverImg)

    @exception_handler
    def postProcessing(self):
        if self.postProcessSegmWin is not None:
            self.postProcessSegmWin.setPosData()
            posData = self.data[self.pos_i]
            lab, delIDs = self.postProcessSegmWin.apply()
            if posData.allData_li[posData.frame_i]['labels'] is None:
                posData.lab = lab.copy()
                self.update_rp()
            else:
                posData.allData_li[posData.frame_i]['labels'] = lab
                self.get_data()

    def next_pos(self):
        self.store_data(debug=False)
        prev_pos_i = self.pos_i
        if self.pos_i < self.num_pos-1:
            self.pos_i += 1
            self.updateSegmDataAutoSaveWorker()
        else:
            self.logger.info('You reached last position.')
            self.pos_i = 0
        self.updatePos()
    
    def updatePos(self):
        self.setSaturBarLabel()
        self.checkManageVersions()
        self.removeAlldelROIsCurrentFrame()
        proceed_cca, never_visited = self.get_data()
        self.initContoursImage()
        self.initTextAnnot()
        self.postProcessing()
        self.updateScrollbars()
        self.updateAllImages(updateFilters=True)
        self.computeSegm()
        self.zoomOut()
        self.restartZoomAutoPilot()

    def prev_pos(self):
        self.store_data(debug=False)
        prev_pos_i = self.pos_i
        if self.pos_i > 0:
            self.pos_i -= 1
            self.updateSegmDataAutoSaveWorker()
        else:
            self.logger.info('You reached first position.')
            self.pos_i = self.num_pos-1
        self.updatePos()

    def updateViewerWindow(self):
        if self.slideshowWin is None:
            return

        if self.slideshowWin.linkWindow is None:
            return

        if not self.slideshowWin.linkWindowCheckbox.isChecked():
            return

        posData = self.data[self.pos_i]
        self.slideshowWin.frame_i = posData.frame_i
        self.slideshowWin.update_img()

    def next_frame(self, warn=True):
        mode = str(self.modeComboBox.currentText())
        isSegmMode =  mode == 'Segmentation and Tracking'
        posData = self.data[self.pos_i]
        if posData.frame_i < posData.SizeT-1:
            if 'lost' in self.titleLabel.text and isSegmMode and warn:
                if self.warnLostCellsAction.isChecked():
                    msg = widgets.myMessageBox()
                    warn_msg = html_utils.paragraph(
                        'Current frame (compared to previous frame) '
                        'has <b>lost the following cells</b>:<br><br>'
                        f'{posData.lost_IDs}<br><br>'
                        'Are you <b>sure</b> you want to continue?<br>'
                    )
                    checkBox = QCheckBox('Do not show again')
                    noButton, yesButton = msg.warning(
                        self, 'Lost cells!', warn_msg,
                        buttonsTexts=('No', 'Yes'),
                        widgets=checkBox
                    )
                    doNotWarnLostCells = not checkBox.isChecked()
                    self.warnLostCellsAction.setChecked(doNotWarnLostCells)
                    if msg.clickedButton == noButton:
                        return
            if 'multiple' in self.titleLabel.text and mode != 'Viewer' and warn:
                msg = widgets.myMessageBox(showCentered=False, wrapText=False)
                warn_msg = html_utils.paragraph(
                    'Current frame contains <b>cells with MULTIPLE contours</b> '
                    '(see title message above the images)<br><br>'
                    'This is potentially an issue indicating that <b>two distant cells '
                    'have been merged</b>.<br><br>'
                    'Are you sure you want to continue?'
                )
                noButton, yesButton = msg.warning(
                   self, 'Multiple contours detected!', warn_msg, 
                   buttonsTexts=('No', 'Yes')
                )
                if msg.cancel or msg.clickedButton==noButton:
                    return

            if posData.frame_i <= 0 and mode == 'Cell cycle analysis':
                IDs = [obj.label for obj in posData.rp]
                editCcaWidget = apps.editCcaTableWidget(
                    posData.cca_df, posData.SizeT, parent=self,
                    title='Initialize cell cycle annotations'
                )
                editCcaWidget.sigApplyChangesFutureFrames.connect(
                    self.applyManualCcaChangesFutureFrames
                )
                editCcaWidget.exec_()
                if editCcaWidget.cancel:
                    return
                if posData.cca_df is not None:
                    is_cca_same_as_stored = (
                        (posData.cca_df == editCcaWidget.cca_df).all(axis=None)
                    )
                    if not is_cca_same_as_stored:
                        reinit_cca = self.warnEditingWithCca_df(
                            'Re-initialize cell cyle annotations first frame',
                            return_answer=True
                        )
                        if reinit_cca:
                            self.remove_future_cca_df(0)
                posData.cca_df = editCcaWidget.cca_df
                self.store_cca_df()

            # Store data for current frame
            if mode != 'Viewer':
                self.store_data(debug=False)
            # Go to next frame
            posData.frame_i += 1
            self.removeAlldelROIsCurrentFrame()
            proceed_cca, never_visited = self.get_data()
            if not proceed_cca:
                posData.frame_i -= 1
                self.get_data()
                return
            self.postProcessing()
            self.tracking(storeUndo=True)
            notEnoughG1Cells, proceed = self.attempt_auto_cca()
            if notEnoughG1Cells or not proceed:
                posData.frame_i -= 1
                self.get_data()
                return
            self.updateAllImages(updateFilters=True)
            self.updateViewerWindow()
            self.setNavigateScrollBarMaximum()
            self.updateScrollbars()
            self.computeSegm()
            self.initGhostObject()
            self.zoomToCells()
        else:
            # Store data for current frame
            if mode != 'Viewer':
                self.store_data(debug=False)
            msg = 'You reached the last segmented frame!'
            self.logger.info(msg)
            self.titleLabel.setText(msg, color=self.titleColor)

    def setNavigateScrollBarMaximum(self):
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        if mode == 'Segmentation and Tracking':
            if posData.last_tracked_i is not None:
                if posData.frame_i > posData.last_tracked_i:
                    self.navigateScrollBar.setMaximum(posData.frame_i+1)
                    self.navSpinBox.setMaximum(posData.frame_i+1)
                else:
                    self.navigateScrollBar.setMaximum(posData.last_tracked_i+1)
                    self.navSpinBox.setMaximum(posData.last_tracked_i+1)
            else:
                self.navigateScrollBar.setMaximum(posData.frame_i+1)
                self.navSpinBox.setMaximum(posData.frame_i+1)
        elif mode == 'Cell cycle analysis':
            if posData.frame_i > self.last_cca_frame_i:
                self.navigateScrollBar.setMaximum(posData.frame_i+1)
                self.navSpinBox.setMaximum(posData.frame_i+1)

    def prev_frame(self):
        posData = self.data[self.pos_i]
        if posData.frame_i > 0:
            # Store data for current frame
            mode = str(self.modeComboBox.currentText())
            if mode != 'Viewer':
                self.store_data(debug=False)
            self.removeAlldelROIsCurrentFrame()
            posData.frame_i -= 1
            _, never_visited = self.get_data()
            self.postProcessing()
            self.tracking()
            self.updateAllImages(updateFilters=True)
            self.updateScrollbars()
            self.zoomToCells()
            self.initGhostObject()
            self.updateViewerWindow()
        else:
            msg = 'You reached the first frame!'
            self.logger.info(msg)
            self.titleLabel.setText(msg, color=self.titleColor)

    def loadSelectedData(self, user_ch_file_paths, user_ch_name):
        data = []
        numPos = len(user_ch_file_paths)
        self.user_ch_file_paths = user_ch_file_paths

        required_ram = myutils.getMemoryFootprint(user_ch_file_paths)
        if required_ram >= 5e8:
            # Disable autosave for data > 500MB
            self.autoSaveToggle.setChecked(False)

        proceed = self.checkMemoryRequirements(required_ram)
        if not proceed:
            self.loadingDataAborted()
            return

        
        self.logger.info(f'Reading {user_ch_name} channel metadata...')
        # Get information from first loaded position
        posData = load.loadData(user_ch_file_paths[0], user_ch_name)
        posData.getBasenameAndChNames()
        posData.buildPaths()

        if posData.ext != '.h5':
            self.lazyLoader.salute = False
            self.lazyLoader.exit = True
            self.lazyLoaderWaitCond.wakeAll()
            self.waitReadH5cond.wakeAll()

        # Get end name of every existing segmentation file
        existingSegmEndNames = set()
        for filePath in user_ch_file_paths:
            _posData = load.loadData(filePath, user_ch_name)
            _posData.getBasenameAndChNames()
            segm_files = load.get_segm_files(_posData.images_path)
            _existingEndnames = load.get_existing_segm_endnames(
                _posData.basename, segm_files
            )
            existingSegmEndNames.update(_existingEndnames)

        selectedSegmEndName = ''
        self.newSegmEndName = ''
        if self.isNewFile or not existingSegmEndNames:
            self.isNewFile = True
            # Remove the 'segm_' part to allow filenameDialog to check if
            # a new file is existing (since we only ask for the part after
            # 'segm_')
            existingEndNames = [
                n.replace('segm', '', 1).replace('_', '', 1)
                for n in existingSegmEndNames
            ]
            if posData.basename.endswith('_'):
                basename = f'{posData.basename}segm'
            else:
                basename = f'{posData.basename}_segm'
            win = apps.filenameDialog(
                basename=basename,
                hintText='Insert a <b>filename</b> for the segmentation file:',
                existingNames=existingEndNames
            )
            win.exec_()
            if win.cancel:
                self.loadingDataAborted()
                return
            self.newSegmEndName = win.entryText
        else:
            if len(existingSegmEndNames) > 1:
                win = apps.QDialogMultiSegmNpz(
                    existingSegmEndNames, self.exp_path, parent=self,
                    addNewFileButton=True, basename=posData.basename
                )
                win.exec_()
                if win.cancel:
                    self.loadingDataAborted()
                    return
                if win.newSegmEndName is None:
                    selectedSegmEndName = win.selectedItemText
                    self.AutoPilotProfile.storeSelectedSegmFile(
                        selectedSegmEndName
                    )
                else:
                    self.newSegmEndName = win.newSegmEndName
                    self.isNewFile = True
            elif len(existingSegmEndNames) == 1:
                selectedSegmEndName = list(existingSegmEndNames)[0]

        posData.loadImgData()
        posData.loadOtherFiles(
            load_segm_data=True,
            load_metadata=True,
            create_new_segm=self.isNewFile,
            new_endname=self.newSegmEndName,
            end_filename_segm=selectedSegmEndName
        )
        self.selectedSegmEndName = selectedSegmEndName
        self.labelBoolSegm = posData.labelBoolSegm
        posData.labelSegmData()

        print('')
        self.logger.info(
            f'Segmentation filename: {posData.segm_npz_path}'
        )

        proceed = posData.askInputMetadata(
            self.num_pos,
            ask_SizeT=self.num_pos==1,
            ask_TimeIncrement=True,
            ask_PhysicalSizes=True,
            singlePos=False,
            save=True, 
            warnMultiPos=True
        )
        if not proceed:
            self.loadingDataAborted()
            return
        
        self.AutoPilotProfile.storeOkAskInputMetadata()

        self.isSegm3D = posData.isSegm3D
        self.SizeT = posData.SizeT
        self.SizeZ = posData.SizeZ
        self.TimeIncrement = posData.TimeIncrement
        self.PhysicalSizeZ = posData.PhysicalSizeZ
        self.PhysicalSizeY = posData.PhysicalSizeY
        self.PhysicalSizeX = posData.PhysicalSizeX
        self.loadSizeS = posData.loadSizeS
        self.loadSizeT = posData.loadSizeT
        self.loadSizeZ = posData.loadSizeZ

        self.overlayLabelsItems = {}
        self.drawModeOverlayLabelsChannels = {}

        self.existingSegmEndNames = existingSegmEndNames
        self.createOverlayLabelsContextMenu(existingSegmEndNames)
        self.overlayLabelsButtonAction.setVisible(True)
        self.createOverlayLabelsItems(existingSegmEndNames)
        self.disableNonFunctionalButtons()

        self.isH5chunk = (
            posData.ext == '.h5'
            and (self.loadSizeT != self.SizeT
                or self.loadSizeZ != self.SizeZ)
        )

        required_ram = posData.checkH5memoryFootprint()*self.loadSizeS
        if required_ram > 0:
            proceed = self.checkMemoryRequirements(required_ram)
            if not proceed:
                self.loadingDataAborted()
                return

        if posData.SizeT == 1:
            self.isSnapshot = True
        else:
            self.isSnapshot = False

        self.progressWin = apps.QDialogWorkerProgress(
            title='Loading data...', parent=self,
            pbarDesc=f'Loading "{user_ch_file_paths[0]}"...'
        )
        self.progressWin.show(self.app)

        func = partial(
            self.startLoadDataWorker, user_ch_file_paths, user_ch_name,
            posData
        )
        QTimer.singleShot(150, func)
    
    def disableNonFunctionalButtons(self):
        if not self.isSegm3D:
            return 

        for item in self.functionsNotTested3D:
            if hasattr(item, 'action'):
                toolButton = item
                action = toolButton.action
                toolButton.setDisabled(True)
            elif hasattr(item, 'toolbar'):
                toolbar = item.toolbar
                action = item
                toolButton = toolbar.widgetForAction(action)
                toolButton.setDisabled(True)    
            else: 
                action = item
            action.setDisabled(True)
            

    @exception_handler
    def startLoadDataWorker(self, user_ch_file_paths, user_ch_name, firstPosData):
        self.funcDescription = 'loading data'

        self.thread = QThread()
        self.loadDataMutex = QMutex()
        self.loadDataWaitCond = QWaitCondition()

        self.loadDataWorker = workers.loadDataWorker(
            self, user_ch_file_paths, user_ch_name, firstPosData
        )

        self.loadDataWorker.moveToThread(self.thread)
        self.loadDataWorker.signals.finished.connect(self.thread.quit)
        self.loadDataWorker.signals.finished.connect(
            self.loadDataWorker.deleteLater
        )
        self.thread.finished.connect(self.thread.deleteLater)

        self.loadDataWorker.signals.finished.connect(
            self.loadDataWorkerFinished
        )
        self.loadDataWorker.signals.progress.connect(self.workerProgress)
        self.loadDataWorker.signals.initProgressBar.connect(
            self.workerInitProgressbar
        )
        self.loadDataWorker.signals.progressBar.connect(
            self.workerUpdateProgressbar
        )
        self.loadDataWorker.signals.critical.connect(
            self.workerCritical
        )
        self.loadDataWorker.signals.dataIntegrityCritical.connect(
            self.loadDataWorkerDataIntegrityCritical
        )
        self.loadDataWorker.signals.dataIntegrityWarning.connect(
            self.loadDataWorkerDataIntegrityWarning
        )
        self.loadDataWorker.signals.sigPermissionError.connect(
            self.workerPermissionError
        )
        self.loadDataWorker.signals.sigWarnMismatchSegmDataShape.connect(
            self.askMismatchSegmDataShape
        )
        self.loadDataWorker.signals.sigRecovery.connect(
            self.askRecoverNotSavedData
        )

        self.thread.started.connect(self.loadDataWorker.run)
        self.thread.start()
    
    def askRecoverNotSavedData(self, posData):
        last_modified_time_unsaved = 'NEVER'
        if os.path.exists(posData.segm_npz_temp_path):
            recovered_file_path = posData.segm_npz_temp_path
            if os.path.exists(posData.segm_npz_path):
                last_modified_time_unsaved = (
                    datetime.datetime.fromtimestamp(
                        os.path.getmtime(posData.segm_npz_path)
                    ).strftime("%a %d. %b. %y - %H:%M:%S")
                )
        else:
            recovered_file_path = posData.acdc_output_temp_csv_path
        
        if os.path.exists(recovered_file_path):
            last_modified_time_saved = (
                datetime.datetime.fromtimestamp(
                    os.path.getmtime(recovered_file_path)
                ).strftime("%a %d. %b. %y - %H:%M:%S")
            )
        else:
            last_modified_time_saved = 'Null'
        
        msg = widgets.myMessageBox(showCentered=False, wrapText=False)
        txt = html_utils.paragraph("""
            Cell-ACDC detected <b>unsaved data</b>.<br><br>
            Do you want to <b>load and recover</b> the unsaved data or 
            load the data that was <b>last saved by the user</b>?
        """)
        details = (f"""
            The unsaved data was created on {last_modified_time_unsaved}\n\n
            The user saved the data last time on {last_modified_time_saved}
        """)
        msg.setDetailedText(details)
        loadUnsavedButton = widgets.reloadPushButton('Recover unsaved data')
        loadSavedButton = widgets.savePushButton('Load saved data')
        infoButton = widgets.infoPushButton('More info...')
        buttons = ('Cancel', loadSavedButton, loadUnsavedButton, infoButton)
        msg.question(
            self.progressWin, 'Recover unsaved data?', txt, 
            buttonsTexts=buttons, showDialog=False
        )
        infoButton.disconnect()
        infoButton.clicked.connect(partial(self.showInfoAutosave, posData))
        msg.exec_()
        if msg.cancel:
            self.loadDataWorker.abort = True
        elif msg.clickedButton == loadUnsavedButton:
            self.loadDataWorker.loadUnsaved = True
        self.loadDataWorker.waitCond.wakeAll()
        # self.AutoPilotProfile.storeLoadSavedData()
    
    def showInfoAutosave(self, posData):
        msg = widgets.myMessageBox(showCentered=False, wrapText=False)
        txt = html_utils.paragraph(f"""
            Cell-ACDC detected unsaved data in a previous session and it stored 
            it because the <b>Autosave</b><br>
            function was active.<br><br>
            You can toggle Autosave ON and OFF from the menu on the top menubar 
            <code>File --> Autosave</code>.<br><br>
            You can find the recovered data in the following folder:<br><br>
            <code>{posData.recoveryFolderPath}</code><br><br>
            This folder <b>will be deleted when you save data the next time</b>.
        """)
        msg.information(self, 'Autosave info', txt)
    
    def askMismatchSegmDataShape(self, posData):
        msg = widgets.myMessageBox(wrapText=False)
        title = 'Segm. data shape mismatch'
        f = '3D' if self.isSegm3D else '2D'
        f = f'{f} over time' if posData.SizeT > 1 else f
        r = '2D' if self.isSegm3D else '3D'
        r = f'{r} over time' if posData.SizeT > 1 else r
        text = html_utils.paragraph(f"""
            The segmentation masks of the first Position that you loaded is 
            <b>{f}</b>,<br>
            while {posData.pos_foldername} is <b>{r}</b>.<br><br>
            The loaded segmentation masks <b>must be</b> either <b>all 3D</b> 
            or <b>all 2D</b>.<br><br>
            Do you want to skip loading this position or cancel the process?
        """)
        _, skipPosButton = msg.warning(
            self, title, text, buttonsTexts=('Cancel', 'Skip this Position')
        )
        if skipPosButton == msg.clickedButton:
            self.loadDataWorker.skipPos = True
        self.loadDataWorker.waitCond.wakeAll()

    def workerPermissionError(self, txt, waitCond):
        msg = widgets.myMessageBox(parent=self)
        msg.setIcon(iconName='SP_MessageBoxCritical')
        msg.setWindowTitle('Permission denied')
        msg.addText(txt)
        msg.addButton('  Ok  ')
        msg.exec_()
        waitCond.wakeAll()

    def loadDataWorkerDataIntegrityCritical(self):
        errTitle = 'All loaded positions contains frames over time!'
        self.titleLabel.setText(errTitle, color='r')

        msg = widgets.myMessageBox(parent=self)

        err_msg = html_utils.paragraph(f"""
            {errTitle}.<br><br>
            To load data that contains frames over time you have to select
            only ONE position.
        """)
        msg.setIcon(iconName='SP_MessageBoxCritical')
        msg.setWindowTitle('Loaded multiple positions with frames!')
        msg.addText(err_msg)
        msg.addButton('Ok')
        msg.show(block=True)

    @exception_handler
    def loadDataWorkerFinished(self, data):
        self.funcDescription = 'loading data worker finished'
        if self.progressWin is not None:
            self.progressWin.workerFinished = True
            self.progressWin.close()
            self.progressWin = None

        if data is None or data=='abort':
            self.loadingDataAborted()
            return
        
        if data[0].onlyEditMetadata:
            self.loadingDataAborted()
            return

        self.pos_i = 0
        self.data = data
        self.gui_createGraphicsItems()
        return True
    
    def checkManageVersions(self):
        posData = self.data[self.pos_i]
        posData.setTempPaths(createFolder=False)
        loaded_acdc_df_filename = os.path.basename(posData.acdc_output_csv_path)

        if os.path.exists(posData.acdc_output_backup_h5_path):
            self.manageVersionsAction.setDisabled(False)
            self.manageVersionsAction.setToolTip(
                f'Load an older version of the `{loaded_acdc_df_filename}` file '
                '(table with annotations and measurements).'
            )
        else:
            self.manageVersionsAction.setDisabled(True)

    def loadingDataCompleted(self):
        self.isDataLoading = True
        posData = self.data[self.pos_i]
        self.updateImageValueFormatter()
        self.checkManageVersions()

        self.setWindowTitle(f'Cell-ACDC - GUI - "{posData.exp_path}"')

        self.guiTabControl.addChannels([posData.user_ch_name])
        self.showPropsDockButton.setDisabled(False)

        self.bottomScrollArea.show()
        self.gui_createStoreStateWorker()
        self.init_segmInfo_df()
        self.connectScrollbars()
        self.initPosAttr()
        self.initMetrics()
        self.initFluoData()
        self.initRealTimeTracker()
        self.createChannelNamesActions()
        self.addActionsLutItemContextMenu(self.imgGrad)
        
        # Scrollbar for opacity of img1 (when overlaying)
        self.img1.alphaScrollbar = self.addAlphaScrollbar(
            self.user_ch_name, self.img1
        )

        self.navigateScrollBar.setSliderPosition(posData.frame_i+1)
        if posData.SizeZ > 1:
            idx = (posData.filename, posData.frame_i)
            try:
                how = posData.segmInfo_df.at[idx, 'which_z_proj_gui']
            except ValueError as e:
                how = posData.segmInfo_df.loc[idx, 'which_z_proj_gui'].iloc[0] 
            self.zProjComboBox.setCurrentText(how)

        # Connect events at the end of loading data process
        self.gui_connectGraphicsEvents()
        if not self.isEditActionsConnected:
            self.gui_connectEditActions()
        self.navSpinBox.connectValueChanged(self.navigateSpinboxValueChanged)

        self.setFramesSnapshotMode()
        if self.isSnapshot:
            self.navSizeLabel.setText(f'/{len(self.data)}') 
        else:
            self.navSizeLabel.setText(f'/{posData.SizeT}')

        self.enableZstackWidgets(posData.SizeZ > 1)
        # self.showHighlightZneighCheckbox()

        self.img1BottomGroupbox.show()

        isLabVisible = self.df_settings.at['isLabelsVisible', 'value'] == 'Yes'
        isRightImgVisible = (
            self.df_settings.at['isRightImageVisible', 'value'] == 'Yes'
        )
        self.updateScrollbars()
        self.openAction.setEnabled(True)
        self.editTextIDsColorAction.setDisabled(False)
        self.imgPropertiesAction.setEnabled(True)
        self.navigateToolBar.setVisible(True)
        self.labelsGrad.showLabelsImgAction.setChecked(isLabVisible)
        self.labelsGrad.showRightImgAction.setChecked(isRightImgVisible)
        if isRightImgVisible:
            self.rightBottomGroupbox.setChecked(True)

        if isRightImgVisible or isLabVisible:
            self.setTwoImagesLayout(True)
        else:
            self.setTwoImagesLayout(False)
        
        self.setBottomLayoutStretch()

        self.readSavedCustomAnnot()
        self.addCustomAnnotButtonAllLoadedPos()
        self.setSaturBarLabel()

        self.initLookupTableLab()
        if self.invertBwAction.isChecked():
            self.invertBw(True)
        self.restoreSavedSettings()

        self.initContoursImage()
        self.initTextAnnot()

        self.update_rp()
        self.updateAllImages()
        
        self.setMetricsFunc()

        self.gui_createLabelRoiItem()

        self.titleLabel.setText(
            'Data successfully loaded.',
            color=self.titleColor
        )

        self.disableNonFunctionalButtons()
        self.setVisible3DsegmWidgets()

        if len(self.data) == 1 and posData.SizeZ > 1 and posData.SizeT == 1:
            self.zSliceCheckbox.setChecked(True)
        else:
            self.zSliceCheckbox.setChecked(False)

        self.labelRoiCircItemLeft.setImageShape(self.currentLab2D.shape)
        self.labelRoiCircItemRight.setImageShape(self.currentLab2D.shape)

        self.retainSpaceSlidersToggled(self.retainSpaceSlidersAction.isChecked())

        self.stopAutomaticLoadingPos()
        self.viewAllCustomAnnotAction.setChecked(True)

        self.updateImageValueFormatter()

        self.setFocusGraphics()
        self.setFocusMain()

        # Overwrite axes viewbox context menu
        self.ax1.vb.menu = self.imgGrad.gradient.menu
        self.ax2.vb.menu = self.labelsGrad.menu

        QTimer.singleShot(200, self.resizeGui)

        self.dataIsLoaded = True
        self.isDataLoading = False
        self.gui_createAutoSaveWorker()
    
    def resizeGui(self):
        self.ax1.vb.state['limits']['xRange'] = [None, None]
        self.ax1.vb.state['limits']['yRange'] = [None, None]
        self.autoRange()
        if self.ax1.getViewBox().state['limits']['xRange'][0] is not None:
            self.bottomScrollArea._resizeVertical()
            return
        (xmin, xmax), (ymin, ymax) = self.ax1.viewRange()
        maxYRange = int((ymax-ymin)*1.5)
        maxXRange = int((xmax-xmin)*1.5)
        self.ax1.setLimits(maxYRange=maxYRange, maxXRange=maxXRange)
        self.bottomScrollArea._resizeVertical()
        QTimer.singleShot(300, self.autoRange)
    
    def setVisible3DsegmWidgets(self):
        self.annotNumZslicesCheckbox.setVisible(self.isSegm3D)
        self.annotNumZslicesCheckboxRight.setVisible(self.isSegm3D)
        if not self.isSegm3D:
            self.annotNumZslicesCheckbox.setChecked(False)
            self.annotNumZslicesCheckboxRight.setChecked(False)
    
    def showHighlightZneighCheckbox(self):
        if self.isSegm3D:
            layout = self.bottomLeftLayout
            # layout.addWidget(self.annotOptionsWidget, 0, 1, 1, 2)
            # # layout.removeWidget(self.drawIDsContComboBox)
            # # layout.addWidget(self.drawIDsContComboBox, 0, 1, 1, 1,
            # #     alignment=Qt.AlignCenter
            # # )
            # layout.addWidget(self.highlightZneighObjCheckbox, 0, 2, 1, 2,
            #     alignment=Qt.AlignRight
            # )
            self.highlightZneighObjCheckbox.show()
            self.highlightZneighObjCheckbox.setChecked(True)
            self.highlightZneighObjCheckbox.toggled.connect(
                self.highlightZneighLabels_cb
            )
            
    def restoreSavedSettings(self):
        if 'how_draw_annotations' in self.df_settings.index:
            how = self.df_settings.at['how_draw_annotations', 'value']
            self.drawIDsContComboBox.setCurrentText(how)
        else:
            self.drawIDsContComboBox.setCurrentText('Draw IDs and contours')
        
        if 'how_draw_right_annotations' in self.df_settings.index:
            how = self.df_settings.at['how_draw_right_annotations', 'value']
            self.annotateRightHowCombobox.setCurrentText(how)
        else:
            self.annotateRightHowCombobox.setCurrentText(
                'Draw IDs and overlay segm. masks'
            )
        
        self.drawAnnotCombobox_to_options()
        self.drawIDsContComboBox_cb(0)
        self.annotateRightHowCombobox_cb(0)
    
    def uncheckAnnotOptions(self, left=True, right=True):
        # Left
        if left:
            self.annotIDsCheckbox.setChecked(False)
            self.annotCcaInfoCheckbox.setChecked(False)
            self.annotContourCheckbox.setChecked(False)
            self.annotSegmMasksCheckbox.setChecked(False)
            self.drawMothBudLinesCheckbox.setChecked(False)
            self.drawNothingCheckbox.setChecked(False)

        # Right 
        if right:
            self.annotIDsCheckboxRight.setChecked(False)
            self.annotCcaInfoCheckboxRight.setChecked(False)
            self.annotContourCheckboxRight.setChecked(False)
            self.annotSegmMasksCheckboxRight.setChecked(False)
            self.drawMothBudLinesCheckboxRight.setChecked(False)
            self.drawNothingCheckboxRight.setChecked(False)

    def setDisabledAnnotOptions(self, disabled):
        # Left
        self.annotIDsCheckbox.setDisabled(disabled)
        self.annotCcaInfoCheckbox.setDisabled(disabled)
        self.annotContourCheckbox.setDisabled(disabled)
        # self.annotSegmMasksCheckbox.setDisabled(disabled)
        self.drawMothBudLinesCheckbox.setDisabled(disabled)
        # self.drawNothingCheckbox.setDisabled(disabled)

        # Right 
        self.annotIDsCheckboxRight.setDisabled(disabled)
        self.annotCcaInfoCheckboxRight.setDisabled(disabled)
        self.annotContourCheckboxRight.setDisabled(disabled)
        # self.annotSegmMasksCheckboxRight.setDisabled(disabled)
        self.drawMothBudLinesCheckboxRight.setDisabled(disabled)
        # self.drawNothingCheckboxRight.setDisabled(disabled)
        
    def drawAnnotCombobox_to_options(self):
        self.uncheckAnnotOptions()

        # Left
        how = self.drawIDsContComboBox.currentText()
        if how.find('IDs') != -1:
            self.annotIDsCheckbox.setChecked(True)
        if how.find('cell cycle info') != -1:
            self.annotCcaInfoCheckbox.setChecked(True) 
        if how.find('contours') != -1:
            self.annotContourCheckbox.setChecked(True) 
        if how.find('segm. masks') != -1:
            self.annotSegmMasksCheckbox.setChecked(True) 
        if how.find('mother-bud lines') != -1:
            self.drawMothBudLinesCheckbox.setChecked(True) 
        if how.find('nothing') != -1:
            self.drawNothingCheckbox.setChecked(True)
        
        # Right
        how = self.annotateRightHowCombobox.currentText()
        if how.find('IDs') != -1:
            self.annotIDsCheckboxRight.setChecked(True)
        if how.find('cell cycle info') != -1:
            self.annotCcaInfoCheckboxRight.setChecked(True) 
        if how.find('contours') != -1:
            self.annotContourCheckboxRight.setChecked(True) 
        if how.find('segm. masks') != -1:
            self.annotSegmMasksCheckboxRight.setChecked(True) 
        if how.find('mother-bud lines') != -1:
            self.drawMothBudLinesCheckboxRight.setChecked(True) 
        if how.find('nothing') != -1:
            self.drawNothingCheckboxRight.setChecked(True)

    def setSaturBarLabel(self, log=True):
        self.statusbar.clearMessage()
        posData = self.data[self.pos_i]
        segmentedChannelname = posData.filename[len(posData.basename):]
        segmFilename = os.path.basename(posData.segm_npz_path)
        segmEndName = segmFilename[len(posData.basename):]
        txt = (
            f'{posData.pos_foldername} || '
            f'Basename: {posData.basename} || '
            f'Segmented channel: {segmentedChannelname} || '
            f'Segmentation file name: {segmEndName}'
        )
        if log:
            self.logger.info(txt)
        self.statusBarLabel.setText(txt)

    def autoRange(self):
        if self.labelsGrad.showLabelsImgAction.isChecked():
            self.ax2.autoRange()
        self.ax1.autoRange()
        
    def resetRange(self):
        if self.ax1_viewRange is None:
            return
        xRange, yRange = self.ax1_viewRange
        if self.labelsGrad.showLabelsImgAction.isChecked():
            self.ax2.vb.setRange(xRange=xRange, yRange=yRange)
        self.ax1.vb.setRange(xRange=xRange, yRange=yRange)
        self.ax1_viewRange = None

    def setFramesSnapshotMode(self):
        self.measurementsMenu.setDisabled(False)
        if self.isSnapshot:
            self.realTimeTrackingToggle.setDisabled(True)
            self.realTimeTrackingToggle.label.setDisabled(True)
            try:
                self.drawIDsContComboBox.currentIndexChanged.disconnect()
            except Exception as e:
                pass

            self.repeatTrackingAction.setDisabled(True)
            self.manualTrackingAction.setDisabled(True)
            self.logger.info('Setting GUI mode to "Snapshots"...')
            self.modeComboBox.clear()
            self.modeComboBox.addItems(['Snapshot'])
            self.modeComboBox.setDisabled(True)
            self.modeMenu.menuAction().setVisible(False)
            self.drawIDsContComboBox.clear()
            self.drawIDsContComboBox.addItems(self.drawIDsContComboBoxSegmItems)
            self.drawIDsContComboBox.setCurrentIndex(1)
            self.modeToolBar.setVisible(False)
            self.modeComboBox.setCurrentText('Snapshot')
            self.annotateToolbar.setVisible(True)
            self.drawIDsContComboBox.currentIndexChanged.connect(
                self.drawIDsContComboBox_cb
            )
            self.showTreeInfoCheckbox.hide()
        else:
            self.annotateToolbar.setVisible(False)
            self.realTimeTrackingToggle.setDisabled(False)
            self.repeatTrackingAction.setDisabled(False)
            self.manualTrackingAction.setDisabled(False)
            self.modeComboBox.setDisabled(False)
            self.modeMenu.menuAction().setVisible(True)
            try:
                self.modeComboBox.activated.disconnect()
                self.modeComboBox.currentIndexChanged.disconnect()
                self.drawIDsContComboBox.currentIndexChanged.disconnect()
            except Exception as e:
                pass
                # traceback.print_exc()
            self.modeComboBox.clear()
            self.modeComboBox.addItems(self.modeItems)
            self.drawIDsContComboBox.clear()
            self.drawIDsContComboBox.addItems(self.drawIDsContComboBoxSegmItems)
            self.modeComboBox.currentIndexChanged.connect(self.changeMode)
            self.modeComboBox.activated.connect(self.clearComboBoxFocus)
            self.drawIDsContComboBox.currentIndexChanged.connect(
                                                    self.drawIDsContComboBox_cb)
            self.modeComboBox.setCurrentText('Viewer')
            self.showTreeInfoCheckbox.show()

    def checkIfAutoSegm(self):
        """
        If there are any frame or position with empty segmentation mask
        ask whether automatic segmentation should be turned ON
        """
        if self.autoSegmAction.isChecked():
            return
        if self.autoSegmDoNotAskAgain:
            return

        ask = False
        for posData in self.data:
            if posData.SizeT > 1:
                for lab in posData.segm_data:
                    if not np.any(lab):
                        ask = True
                        txt = 'frames'
                        break
            else:
                if not np.any(posData.segm_data):
                    ask = True
                    txt = 'positions'
                    break

        if not ask:
            return

        questionTxt = html_utils.paragraph(
            f'Some or all loaded {txt} contain <b>empty segmentation masks</b>.<br><br>'
            'Do you want to <b>activate automatic segmentation</b><sup>*</sup> '
            f'when visiting these {txt}?<br><br>'
            '<i>* Automatic segmentation can always be turned ON/OFF from the menu<br>'
            '  <code>Edit --> Segmentation --> Enable automatic segmentation</code><br><br></i>'
            f'NOTE: you can automatically segment all {txt} using the<br>'
            '    segmentation module.'
        )
        msg = widgets.myMessageBox(wrapText=False)
        noButton, yesButton = msg.question(
            self, 'Automatic segmentation?', questionTxt,
            buttonsTexts=('No', 'Yes')
        )
        if msg.clickedButton == yesButton:
            self.autoSegmAction.setChecked(True)
        else:
            self.autoSegmDoNotAskAgain = True
            self.autoSegmAction.setChecked(False)

    def init_segmInfo_df(self):
        for posData in self.data:
            if posData is None:
                # posData is None when computing measurements with the utility
                # and with timelapse data
                continue
            if posData.SizeZ > 1 and posData.segmInfo_df is not None:
                if 'z_slice_used_gui' not in posData.segmInfo_df.columns:
                    posData.segmInfo_df['z_slice_used_gui'] = (
                        posData.segmInfo_df['z_slice_used_dataPrep']
                    )
                if 'which_z_proj_gui' not in posData.segmInfo_df.columns:
                    posData.segmInfo_df['which_z_proj_gui'] = (
                        posData.segmInfo_df['which_z_proj']
                    )
                posData.segmInfo_df['resegmented_in_gui'] = False
                posData.segmInfo_df.to_csv(posData.segmInfo_df_csv_path)

            NO_segmInfo = (
                posData.segmInfo_df is None
                or posData.filename not in posData.segmInfo_df.index
            )
            if NO_segmInfo and posData.SizeZ > 1:
                filename = posData.filename
                df = myutils.getDefault_SegmInfo_df(posData, filename)
                if posData.segmInfo_df is None:
                    posData.segmInfo_df = df
                else:
                    posData.segmInfo_df = pd.concat([df, posData.segmInfo_df])
                    unique_idx = ~posData.segmInfo_df.index.duplicated()
                    posData.segmInfo_df = posData.segmInfo_df[unique_idx]
                posData.segmInfo_df.to_csv(posData.segmInfo_df_csv_path)

    def connectScrollbars(self):
        self.t_label.show()
        self.navigateScrollBar.show()
        self.navigateScrollBar.setDisabled(False)

        if self.data[0].SizeZ > 1:
            self.enableZstackWidgets(True)
            self.zSliceScrollBar.setMaximum(self.data[0].SizeZ-1)
            self.zSliceSpinbox.setMaximum(self.data[0].SizeZ)
            self.SizeZlabel.setText(f'/{self.data[0].SizeZ}')
            try:
                self.zSliceScrollBar.actionTriggered.disconnect()
                self.zSliceScrollBar.sliderReleased.disconnect()
                self.zProjComboBox.currentTextChanged.disconnect()
                self.zProjComboBox.activated.disconnect()
            except Exception as e:
                pass
            self.zSliceScrollBar.actionTriggered.connect(
                self.zSliceScrollBarActionTriggered
            )
            self.zSliceScrollBar.sliderReleased.connect(
                self.zSliceScrollBarReleased
            )
            self.zProjComboBox.currentTextChanged.connect(self.updateZproj)
            self.zProjComboBox.activated.connect(self.clearComboBoxFocus)

        posData = self.data[self.pos_i]
        if posData.SizeT == 1:
            self.t_label.setText('Position n.')
            self.navigateScrollBar.setMinimum(1)
            self.navigateScrollBar.setMaximum(len(self.data))
            self.navigateScrollBar.setAbsoluteMaximum(len(self.data))
            self.navSpinBox.setMaximum(len(self.data))
            try:
                self.navigateScrollBar.sliderMoved.disconnect()
                self.navigateScrollBar.sliderReleased.disconnect()
                self.navigateScrollBar.actionTriggered.disconnect()
            except TypeError:
                pass
            self.navigateScrollBar.sliderMoved.connect(
                self.PosScrollBarMoved
            )
            self.navigateScrollBar.sliderReleased.connect(
                self.PosScrollBarReleased
            )
            self.navigateScrollBar.actionTriggered.connect(
                self.PosScrollBarAction
            )
        else:
            self.navigateScrollBar.setMinimum(1)
            self.navigateScrollBar.setAbsoluteMaximum(posData.SizeT)
            if posData.last_tracked_i is not None:
                self.navigateScrollBar.setMaximum(posData.last_tracked_i+1)
                self.navSpinBox.setMaximum(posData.last_tracked_i+1)
            try:
                self.navigateScrollBar.sliderMoved.disconnect()
                self.navigateScrollBar.sliderReleased.disconnect()
                self.navigateScrollBar.actionTriggered.disconnect()
            except Exception as e:
                pass
            self.t_label.setText('Frame n.')
            self.navigateScrollBar.sliderMoved.connect(
                self.framesScrollBarMoved
            )
            self.navigateScrollBar.sliderReleased.connect(
                self.framesScrollBarReleased
            )
            self.navigateScrollBar.actionTriggered.connect(
                self.framesScrollBarActionTriggered
            )

    def zSliceScrollBarActionTriggered(self, action):
        singleMove = (
            action == SliderSingleStepAdd
            or action == SliderSingleStepSub
            or action == SliderPageStepAdd
            or action == SliderPageStepSub
        )
        if singleMove:
            self.update_z_slice(self.zSliceScrollBar.sliderPosition())
        elif action == SliderMove:
            if self.zSliceScrollBarStartedMoving and self.isSegm3D:
                self.clearAx1Items(onlyHideText=True)
                self.clearAx2Items(onlyHideText=True)
            posData = self.data[self.pos_i]
            idx = (posData.filename, posData.frame_i)
            z = self.zSliceScrollBar.sliderPosition()
            posData.segmInfo_df.at[idx, 'z_slice_used_gui'] = z
            self.zSliceSpinbox.setValueNoEmit(z+1)
            img = self.getImage()
            self.img1.setImage(img)
            self.setOverlayImages()
            if self.labelsGrad.showLabelsImgAction.isChecked():
                self.img2.setImage(posData.lab, z=z, autoLevels=False)
            self.updateViewerWindow()
            self.setTextAnnotZsliceScrolling()
            self.setGraphicalAnnotZsliceScrolling()
            self.drawPointsLayers(computePointsLayers=False)
            self.zSliceScrollBarStartedMoving = False

    def zSliceScrollBarReleased(self):
        self.tempLayerImg1.setImage(self.emptyLab)
        self.tempLayerRightImage.setImage(self.emptyLab)
        self.zSliceScrollBarStartedMoving = True
        self.update_z_slice(self.zSliceScrollBar.sliderPosition())
    
    def onZsliceSpinboxValueChange(self, value):
        self.zSliceScrollBar.setSliderPosition(value-1)

    def update_z_slice(self, z):
        posData = self.data[self.pos_i]
        idx = (posData.filename, posData.frame_i)
        posData.segmInfo_df.at[idx, 'z_slice_used_gui'] = z
        self.updateAllImages(computePointsLayers=False)

    def updateOverlayZslice(self, z):
        self.setOverlayImages()

    def updateOverlayZproj(self, how):
        if how.find('max') != -1 or how == 'same as above':
            self.overlay_z_label.setDisabled(True)
            self.zSliceOverlay_SB.setDisabled(True)
        else:
            self.overlay_z_label.setDisabled(False)
            self.zSliceOverlay_SB.setDisabled(False)
        self.setOverlayImages()

    def updateZproj(self, how):
        for p, posData in enumerate(self.data[self.pos_i:]):
            idx = (posData.filename, posData.frame_i)
            posData.segmInfo_df.at[idx, 'which_z_proj_gui'] = how
        posData = self.data[self.pos_i]
        if how == 'single z-slice':
            self.zSliceScrollBar.setDisabled(False)
            self.zSliceSpinbox.setDisabled(False)
            self.zSliceCheckbox.setDisabled(False)
            self.update_z_slice(self.zSliceScrollBar.sliderPosition())
        else:
            self.zSliceScrollBar.setDisabled(True)
            self.zSliceSpinbox.setDisabled(True)
            self.zSliceCheckbox.setDisabled(True)
            self.updateAllImages()
    
    def clearAx2Items(self, onlyHideText=False):
        self.ax2_binnedIDs_ScatterPlot.clear()
        self.ax2_ripIDs_ScatterPlot.clear()
        self.ax2_contoursImageItem.clear()
        self.textAnnot[1].clear()
        self.ax2_newMothBudLinesItem.setData([], [])
        self.ax2_oldMothBudLinesItem.setData([], [])
        self.ax2_lostObjScatterItem.setData([], [])
    
    def clearAx1Items(self, onlyHideText=False):
        self.ax1_binnedIDs_ScatterPlot.clear()
        self.ax1_ripIDs_ScatterPlot.clear()
        self.labelsLayerImg1.clear()
        self.labelsLayerRightImg.clear()
        self.keepIDsTempLayerLeft.clear()
        self.keepIDsTempLayerRight.clear()
        self.highLightIDLayerImg1.clear()
        self.highLightIDLayerRightImage.clear()
        self.searchedIDitemLeft.clear()
        self.searchedIDitemRight.clear()
        self.ax1_contoursImageItem.clear()
        self.textAnnot[0].clear()
        self.ax1_newMothBudLinesItem.setData([], [])
        self.ax1_oldMothBudLinesItem.setData([], [])
        self.ax1_lostObjScatterItem.setData([], [])
        self.ccaFailedScatterItem.setData([], [])
        
        self.clearPointsLayers()

        self.clearOverlayLabelsItems()
    
    def clearPointsLayers(self):
        for action in self.pointsLayersToolbar.actions()[1:]:
            try:
                action.scatterItem.setData([], [])
                # action.pointsData = {}
            except Exception as e:
                continue

    def clearOverlayLabelsItems(self):
        for segmEndname, drawMode in self.drawModeOverlayLabelsChannels.items():
            items = self.overlayLabelsItems[segmEndname]
            imageItem, contoursItem, gradItem = items
            imageItem.clear()
            contoursItem.clear()

    def clearAllItems(self):
        self.clearAx1Items()
        self.clearAx2Items()

    def clearCurvItems(self, removeItems=True):
        try:
            posData = self.data[self.pos_i]
            curvItems = zip(posData.curvPlotItems,
                            posData.curvAnchorsItems,
                            posData.curvHoverItems)
            for plotItem, curvAnchors, hoverItem in curvItems:
                plotItem.setData([], [])
                curvAnchors.setData([], [])
                hoverItem.setData([], [])
                if removeItems:
                    self.ax1.removeItem(plotItem)
                    self.ax1.removeItem(curvAnchors)
                    self.ax1.removeItem(hoverItem)

            if removeItems:
                posData.curvPlotItems = []
                posData.curvAnchorsItems = []
                posData.curvHoverItems = []
        except AttributeError:
            # traceback.print_exc()
            pass
    
    # @exec_time
    def splineToObj(self, xxA=None, yyA=None, isRightClick=False):
        posData = self.data[self.pos_i]
        # Store undo state before modifying stuff
        self.storeUndoRedoStates(False)

        if isRightClick:
            xxS, yyS = self.curvPlotItem.getData()
            if xxS is None:
                self.setUncheckedAllButtons()
                return
            N = len(xxS)
            self.smoothAutoContWithSpline(n=int(N*0.05))

        xxS, yyS = self.getClosedSplineCoords()

        self.setBrushID()
        newIDMask = np.zeros(self.currentLab2D.shape, bool)
        rr, cc = skimage.draw.polygon(yyS, xxS)
        newIDMask[rr, cc] = True
        newIDMask[self.currentLab2D!=0] = False
        self.currentLab2D[newIDMask] = posData.brushID
        self.set_2Dlab(self.currentLab2D)

    def addFluoChNameContextMenuAction(self, ch_name):
        posData = self.data[self.pos_i]
        allTexts = [
            action.text() for action in self.chNamesQActionGroup.actions()
        ]
        if ch_name not in allTexts:
            action = QAction(self)
            action.setText(ch_name)
            action.setCheckable(True)
            self.chNamesQActionGroup.addAction(action)
            action.setChecked(True)
            self.fluoDataChNameActions.append(action)

    def computeSegm(self, force=False):
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        if mode == 'Viewer' or mode == 'Cell cycle analysis':
            return

        if np.any(posData.lab) and not force:
            # Do not compute segm if there is already a mask
            return

        if not self.autoSegmAction.isChecked():
            # Compute segmentations that have an open window
            if self.segmModelName == 'randomWalker':
                self.randomWalkerWin.getImage()
                self.randomWalkerWin.computeMarkers()
                self.randomWalkerWin.computeSegm()
                self.update_rp()
                self.tracking(enforce=True)
                if self.isSnapshot:
                    self.fixCcaDfAfterEdit('Random Walker segmentation')
                    self.updateAllImages()
                else:
                    self.warnEditingWithCca_df('Random Walker segmentation')
                self.store_data()
            else:
                return

        self.repeatSegm(model_name=self.segmModelName)

    def initImgCmap(self):
        if not 'img_cmap' in self.df_settings.index:
            self.df_settings.at['img_cmap', 'value'] = 'grey'
        self.imgCmapName = self.df_settings.at['img_cmap', 'value']
        self.imgCmap = self.imgGrad.cmaps[self.imgCmapName]
        if self.imgCmapName != 'grey':
            # To ensure mapping to colors we need to normalize image
            self.normalizeByMaxAction.setChecked(True)

    def initGlobalAttr(self):
        self.setOverlayColors()

        self.initImgCmap()

        # Colormap
        self.setLut()

        self.fluoDataChNameActions = []

        self.filteredData = {}

        self.splineHoverON = False
        self.tempSegmentON = False
        self.isCtrlDown = False
        self.typingEditID = False
        self.isShiftDown = False
        self.ghostObject = None
        self.autoContourHoverON = False
        self.navigateScrollBarStartedMoving = True
        self.zSliceScrollBarStartedMoving = True
        self.labelRoiRunning = False
        self.editIDmergeIDs = True
        self.doNotAskAgainExistingID = False
        self.highlightedIDopts = None
        self.keptObjectsIDs = widgets.KeptObjectIDsList(
            self.keptIDsLineEdit, self.keepIDsConfirmAction
        )
        self.imgValueFormatter = 'd'
        self.rawValueFormatter = 'd'
        self.lastHoverID = -1

        # Second channel used by cellpose
        self.secondChannelName = None

        self.ax1_viewRange = None
        self.measurementsWin = None

        self.model_kwargs = None
        self.segmModelName = None
        self.labelRoiModel = None
        self.autoSegmDoNotAskAgain = False
        self.labelRoiGarbageWorkers = []
        self.labelRoiActiveWorkers = []

        self.clickedOnBud = False
        self.postProcessSegmWin = None

        self.UserEnforced_DisabledTracking = False
        self.UserEnforced_Tracking = False

        self.ax1BrushHoverID = 0

        self.last_pos_i = -1
        self.last_frame_i = -1

        # Plots items
        self.isMouseDragImg2 = False
        self.isMouseDragImg1 = False
        self.isMovingLabel = False
        self.isRightClickDragImg1 = False

        self.cca_df_colnames = list(base_cca_df.keys())
        self.cca_df_dtypes = [
            str, int, int, str, int, int, bool, bool, int
        ]
        self.cca_df_default_values = list(base_cca_df.values())
        self.cca_df_int_cols = [
            'generation_num',
            'relative_ID',
            'emerg_frame_i',
            'division_frame_i'
        ]
        self.cca_df_bool_col = [
            'is_history_known',
            'corrected_assignment'
        ]
    
    def initMetricsToSave(self, posData):
        posData.setLoadedChannelNames()

        if self.metricsToSave is None:
            # self.metricsToSave means that the user did not set 
            # through setMeasurements dialog --> save all measurements
            self.metricsToSave = {chName:[] for chName in posData.loadedChNames}
            for chName in posData.loadedChNames:
                metrics_desc, bkgr_val_desc = measurements.standard_metrics_desc(
                    posData.SizeZ>1, chName, isSegm3D=self.isSegm3D
                )
                self.metricsToSave[chName].extend(metrics_desc.keys())
                self.metricsToSave[chName].extend(bkgr_val_desc.keys())

                custom_metrics_desc = measurements.custom_metrics_desc(
                    posData.SizeZ>1, chName, posData=posData, 
                    isSegm3D=self.isSegm3D, return_combine=False
                )
                self.metricsToSave[chName].extend(
                    custom_metrics_desc.keys()
                )
        
        # Get metrics parameters --> function name, how etc
        self.metrics_func, _ = measurements.standard_metrics_func()
        self.custom_func_dict = measurements.get_custom_metrics_func()
        params = measurements.get_metrics_params(
            self.metricsToSave, self.metrics_func, self.custom_func_dict
        )
        (bkgr_metrics_params, foregr_metrics_params, 
        concentration_metrics_params, custom_metrics_params) = params
        self.bkgr_metrics_params = bkgr_metrics_params
        self.foregr_metrics_params = foregr_metrics_params
        self.concentration_metrics_params = concentration_metrics_params
        self.custom_metrics_params = custom_metrics_params

    def initMetrics(self):
        self.logger.info('Initializing measurements...')
        self.chNamesToSkip = []
        self.metricsToSkip = {}
        # At the moment we don't know how many channels the user will load -->
        # we set the measurements to save either at setMeasurements dialog
        # or at initMetricsToSave
        self.metricsToSave = None
        self.regionPropsToSave = measurements.get_props_names()
        if self.isSegm3D:
            self.regionPropsToSave = measurements.get_props_names_3D()
        else:
            self.regionPropsToSave = measurements.get_props_names()  
        self.mixedChCombineMetricsToSkip = []
        posData = self.data[self.pos_i]
        self.sizeMetricsToSave = list(
            measurements.get_size_metrics_desc(
                self.isSegm3D, posData.SizeT>1
            ).keys()
        )
        exp_path = posData.exp_path
        posFoldernames = myutils.get_pos_foldernames(exp_path)
        for pos in posFoldernames:
            images_path = os.path.join(exp_path, pos, 'Images')
            for file in myutils.listdir(images_path):
                if not file.endswith('custom_combine_metrics.ini'):
                    continue
                filePath = os.path.join(images_path, file)
                configPars = load.read_config_metrics(filePath)

                posData.combineMetricsConfig = load.add_configPars_metrics(
                    configPars, posData.combineMetricsConfig
                )

    def initPosAttr(self):
        exp_path = self.data[self.pos_i].exp_path
        pos_foldernames = myutils.get_pos_foldernames(exp_path)
        if len(pos_foldernames) == 1:
            self.loadPosAction.setDisabled(True)
        else:
            self.loadPosAction.setDisabled(False)

        for p, posData in enumerate(self.data):
            self.pos_i = p
            posData.curvPlotItems = []
            posData.curvAnchorsItems = []
            posData.curvHoverItems = []

            posData.HDDmaxID = np.max(posData.segm_data)

            # Decision on what to do with changes to future frames attr
            posData.doNotShowAgain_EditID = False
            posData.UndoFutFrames_EditID = False
            posData.applyFutFrames_EditID = False

            posData.doNotShowAgain_RipID = False
            posData.UndoFutFrames_RipID = False
            posData.applyFutFrames_RipID = False

            posData.doNotShowAgain_DelID = False
            posData.UndoFutFrames_DelID = False
            posData.applyFutFrames_DelID = False

            posData.doNotShowAgain_keepID = False
            posData.UndoFutFrames_keepID = False
            posData.applyFutFrames_keepID = False

            posData.includeUnvisitedInfo = {
                'Delete ID': False, 'Edit ID': False, 'Keep ID': False
            }

            posData.doNotShowAgain_BinID = False
            posData.UndoFutFrames_BinID = False
            posData.applyFutFrames_BinID = False

            posData.disableAutoActivateViewerWindow = False
            posData.new_IDs = []
            posData.lost_IDs = []
            posData.multiBud_mothIDs = [2]
            posData.UndoRedoStates = [[] for _ in range(posData.SizeT)]
            posData.UndoRedoCcaStates = [[] for _ in range(posData.SizeT)]

            posData.ol_data_dict = {}
            posData.ol_data = None

            posData.ol_labels_data = None

            posData.allData_li = [{
                'regionprops': None,
                'labels': None,
                'acdc_df': None,
                'delROIs_info': { 'rois': [], 'delMasks': [], 'delIDsROI': []},
                'IDs': []
            } for i in range(posData.SizeT)]

            posData.ccaStatus_whenEmerged = {}

            posData.frame_i = 0
            posData.brushID = 0
            posData.binnedIDs = set()
            posData.ripIDs = set()
            posData.cca_df = None
            if posData.last_tracked_i is not None:
                last_tracked_num = posData.last_tracked_i+1
                # Load previous session data
                # Keep track of which ROIs have already been added
                # in previous frame
                delROIshapes = [[] for _ in range(posData.SizeT)]
                for i in range(last_tracked_num):
                    posData.frame_i = i
                    self.get_data()
                    self.store_data(enforce=True, autosave=False)
                    # self.load_delROIs_info(delROIshapes, last_tracked_num)

                # Ask whether to resume from last frame
                if last_tracked_num>1:
                    msg = widgets.myMessageBox()
                    txt = html_utils.paragraph(
                        'The system detected a previous session ended '
                        f'at frame {last_tracked_num}.<br><br>'
                        f'Do you want to <b>resume from frame '
                        f'{last_tracked_num}?</b>'
                    )
                    noButton, yesButton = msg.question(
                        self, 'Start from last session?', txt,
                        buttonsTexts=(' No ', 'Yes')
                    )
                    self.AutoPilotProfile.storeClickMessageBox(
                        'Start from last session?', msg.clickedButton.text()
                    )
                    if msg.clickedButton == yesButton:
                        posData.frame_i = posData.last_tracked_i
                    else:
                        posData.frame_i = 0

        # Back to first position
        self.pos_i = 0
        self.get_data(debug=False)
        self.store_data(autosave=False)
        # self.updateAllImages()

        # Link Y and X axis of both plots to scroll zoom and pan together
        self.ax2.vb.setYLink(self.ax1.vb)
        self.ax2.vb.setXLink(self.ax1.vb)

        self.setAllIDs()

    def navigateSpinboxValueChanged(self, value):
        self.navigateScrollBar.setSliderPosition(value)
        if self.isSnapshot:
            self.PosScrollBarMoved(value)
        else:
            self.navigateScrollBarStartedMoving = True
            self.framesScrollBarMoved(value)
    
    def navigateSpinboxEditingFinished(self):
        if self.isSnapshot:
            self.PosScrollBarReleased()
        else:
            self.framesScrollBarReleased()

    def PosScrollBarAction(self, action):
        if action == SliderSingleStepAdd:
            self.next_cb()
        elif action == SliderSingleStepSub:
            self.prev_cb()
        elif action == SliderPageStepAdd:
            self.PosScrollBarReleased()
        elif action == SliderPageStepSub:
            self.PosScrollBarReleased()

    def PosScrollBarMoved(self, pos_n):
        self.pos_i = pos_n-1
        self.updateFramePosLabel()
        proceed_cca, never_visited = self.get_data()
        self.updateAllImages()
        self.setSaturBarLabel()

    def PosScrollBarReleased(self):
        self.pos_i = self.navigateScrollBar.sliderPosition()-1
        self.updateFramePosLabel()
        self.updatePos()

    def framesScrollBarActionTriggered(self, action):
        if action == SliderSingleStepAdd:
            # Clicking on dialogs triggered by next_cb might trigger
            # pressEvent of navigateQScrollBar, avoid that
            self.navigateScrollBar.disableCustomPressEvent()
            self.next_cb()
            QTimer.singleShot(100, self.navigateScrollBar.enableCustomPressEvent)
        elif action == SliderSingleStepSub:
            self.prev_cb()
        elif action == SliderPageStepAdd:
            self.framesScrollBarReleased()
        elif action == SliderPageStepSub:
            self.framesScrollBarReleased()

    def framesScrollBarMoved(self, frame_n):
        posData = self.data[self.pos_i]
        posData.frame_i = frame_n-1
        if posData.allData_li[posData.frame_i]['labels'] is None:
            if posData.frame_i < len(posData.segm_data):
                posData.lab = posData.segm_data[posData.frame_i]
            else:
                posData.lab = np.zeros_like(posData.segm_data[0])
        else:
            posData.lab = posData.allData_li[posData.frame_i]['labels']

        img = self.getImage()
        self.img1.setImage(img)
        if self.overlayButton.isChecked():
            self.setOverlayImages()

        if self.navigateScrollBarStartedMoving:
            self.clearAllItems()

        self.navSpinBox.setValueNoEmit(posData.frame_i+1)
        if self.labelsGrad.showLabelsImgAction.isChecked():
            self.img2.setImage(posData.lab, z=self.z_lab(), autoLevels=False)
        self.updateLookuptable()
        self.updateFramePosLabel()
        self.updateViewerWindow()
        self.navigateScrollBarStartedMoving = False

    def framesScrollBarReleased(self):
        self.navigateScrollBarStartedMoving = True
        posData = self.data[self.pos_i]
        posData.frame_i = self.navigateScrollBar.sliderPosition()-1
        self.updateFramePosLabel()
        proceed_cca, never_visited = self.get_data()
        self.updateAllImages(updateFilters=True)

    def unstore_data(self):
        posData = self.data[self.pos_i]
        posData.allData_li[posData.frame_i] = {
            'regionprops': [],
            'labels': None,
            'acdc_df': None,
            'delROIs_info': {
                'rois': [], 'delMasks': [], 'delIDsROI': []
            }
        }

    @exception_handler
    def store_data(
            self, pos_i=None, enforce=True, debug=False, mainThread=True,
            autosave=True
        ):
        pos_i = self.pos_i if pos_i is None else pos_i
        posData = self.data[pos_i]
        if posData.frame_i < 0:
            # In some cases we set frame_i = -1 and then call next_frame
            # to visualize frame 0. In that case we don't store data
            # for frame_i = -1
            return

        mode = str(self.modeComboBox.currentText())

        if mode == 'Viewer' and not enforce:
            return

        posData.allData_li[posData.frame_i]['regionprops'] = posData.rp.copy()
        posData.allData_li[posData.frame_i]['labels'] = posData.lab.copy()
        posData.allData_li[posData.frame_i]['IDs'] = posData.IDs.copy()

        # Store dynamic metadata
        is_cell_dead_li = [False]*len(posData.rp)
        is_cell_excluded_li = [False]*len(posData.rp)
        IDs = [0]*len(posData.rp)
        xx_centroid = [0]*len(posData.rp)
        yy_centroid = [0]*len(posData.rp)
        if self.isSegm3D:
            zz_centroid = [0]*len(posData.rp)
        areManuallyEdited = [0]*len(posData.rp)
        editedNewIDs = [vals[2] for vals in posData.editID_info]
        for i, obj in enumerate(posData.rp):
            is_cell_dead_li[i] = obj.dead
            is_cell_excluded_li[i] = obj.excluded
            IDs[i] = obj.label
            xx_centroid[i] = int(self.getObjCentroid(obj.centroid)[1])
            yy_centroid[i] = int(self.getObjCentroid(obj.centroid)[0])
            if self.isSegm3D:
                zz_centroid[i] = int(obj.centroid[0])
            if obj.label in editedNewIDs:
                areManuallyEdited[i] = 1

        posData.STOREDmaxID = max(IDs, default=0)

        acdc_df = posData.allData_li[posData.frame_i]['acdc_df']
        if acdc_df is None:
            posData.allData_li[posData.frame_i]['acdc_df'] = pd.DataFrame(
                {
                    'Cell_ID': IDs,
                    'is_cell_dead': is_cell_dead_li,
                    'is_cell_excluded': is_cell_excluded_li,
                    'x_centroid': xx_centroid,
                    'y_centroid': yy_centroid,
                    'was_manually_edited': areManuallyEdited
                }
            ).set_index('Cell_ID')
            if self.isSegm3D:
                posData.allData_li[posData.frame_i]['acdc_df']['z_centroid'] = (
                    zz_centroid
                )
        else:
            # Filter or add IDs that were not stored yet
            acdc_df = acdc_df.drop(columns=['time_seconds'], errors='ignore')
            acdc_df = acdc_df.reindex(IDs, fill_value=0)
            acdc_df['is_cell_dead'] = is_cell_dead_li
            acdc_df['is_cell_excluded'] = is_cell_excluded_li
            acdc_df['x_centroid'] = xx_centroid
            acdc_df['y_centroid'] = yy_centroid
            if self.isSegm3D:
                acdc_df['z_centroid'] = zz_centroid
            acdc_df['was_manually_edited'] = areManuallyEdited
            posData.allData_li[posData.frame_i]['acdc_df'] = acdc_df
    
        self.pointsLayerDataToDf(posData)
        self.store_cca_df(pos_i=pos_i, mainThread=mainThread, autosave=autosave)

    def nearest_point_2Dyx(self, points, all_others):
        """
        Given 2D array of [y, x] coordinates points and all_others return the
        [y, x] coordinates of the two points (one from points and one from all_others)
        that have the absolute minimum distance
        """
        # Compute 3D array where each ith row of each kth page is the element-wise
        # difference between kth row of points and ith row in all_others array.
        # (i.e. diff[k,i] = points[k] - all_others[i])
        diff = points[:, np.newaxis] - all_others
        # Compute 2D array of distances where
        # dist[i, j] = euclidean dist (points[i],all_others[j])
        dist = np.linalg.norm(diff, axis=2)
        # Compute i, j indexes of the absolute minimum distance
        i, j = np.unravel_index(dist.argmin(), dist.shape)
        nearest_point = all_others[j]
        point = points[i]
        min_dist = np.min(dist)
        return min_dist, nearest_point

    def checkMultiBudMoth(self, draw=False):
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        if mode.find('Cell cycle') == -1:
            posData.multiBud_mothIDs = []
            return

        cca_df_S = posData.cca_df[posData.cca_df['cell_cycle_stage'] == 'S']
        cca_df_S_bud = cca_df_S[cca_df_S['relationship'] == 'bud']
        relIDs_of_S_bud = cca_df_S_bud['relative_ID']
        duplicated_relIDs_mask = relIDs_of_S_bud.duplicated(keep=False)
        duplicated_cca_df_S = cca_df_S_bud[duplicated_relIDs_mask]
        multiBud_mothIDs = duplicated_cca_df_S['relative_ID'].unique()
        posData.multiBud_mothIDs = multiBud_mothIDs
        multiBudInfo = []
        for multiBud_ID in multiBud_mothIDs:
            duplicatedBuds_df = cca_df_S_bud[
                                    cca_df_S_bud['relative_ID'] == multiBud_ID]
            duplicatedBudIDs = duplicatedBuds_df.index.to_list()
            info = f'Mother ID {multiBud_ID} has bud IDs {duplicatedBudIDs}'
            multiBudInfo.append(info)
        if multiBudInfo:
            multiBudInfo_format = '\n'.join(multiBudInfo)
            self.MultiBudMoth_msg = QMessageBox()
            self.MultiBudMoth_msg.setWindowTitle(
                                  'Mother with multiple buds assigned to it!')
            self.MultiBudMoth_msg.setText(multiBudInfo_format)
            self.MultiBudMoth_msg.setIcon(self.MultiBudMoth_msg.Warning)
            self.MultiBudMoth_msg.setDefaultButton(self.MultiBudMoth_msg.Ok)
            self.MultiBudMoth_msg.exec_()

    def isCurrentFrameCcaVisited(self):
        posData = self.data[self.pos_i]
        curr_df = posData.allData_li[posData.frame_i]['acdc_df']
        return curr_df is not None and 'cell_cycle_stage' in curr_df.columns

    def warnScellsGone(self, ScellsIDsGone, frame_i):
        msg = widgets.myMessageBox()
        text = html_utils.paragraph(f"""
            In the next frame the followning cells' IDs in S/G2/M
            (highlighted with a yellow contour) <b>will disappear</b>:<br><br>
            {ScellsIDsGone}<br><br>
            If the cell <b>does not exist</b> you might have deleted it at some point. 
            If that's the case, then try to go to some previous frames and reset 
            the cell cycle annotations there (button on the top toolbar).<br><br>
            These cells are either buds or mother whose <b>related IDs will not
            disappear</b>. This is likely due to cell division happening in
            previous frame and the divided bud or mother will be
            washed away.<br><br>
            If you decide to continue these cells will be <b>automatically
            annotated as divided at frame number {frame_i}</b>.<br><br>
            Do you want to continue?
        """)
        _, yesButton, noButton = msg.warning(
           self, 'Cells in "S/G2/M" disappeared!', text,
           buttonsTexts=('Cancel', 'Yes', 'No')
        )
        return msg.clickedButton == yesButton

    def checkScellsGone(self):
        """Check if there are cells in S phase whose relative disappear in
        current frame. Allow user to choose between automatically assign
        division to these cells or cancel and not visit the frame.

        Returns
        -------
        bool
            False if there are no cells disappeared or the user decided
            to accept automatic division.
        """
        automaticallyDividedIDs = []

        mode = str(self.modeComboBox.currentText())
        if mode.find('Cell cycle') == -1:
            # No cell cycle analysis mode --> do nothing
            return False, automaticallyDividedIDs

        posData = self.data[self.pos_i]

        if posData.allData_li[posData.frame_i]['labels'] is None:
            # Frame never visited/checked in segm mode --> autoCca_df will raise
            # a critical message
            return False, automaticallyDividedIDs

        # Check if there are S cells that either only mother or only
        # bud disappeared and automatically assign division to it
        # or abort visiting this frame
        prev_acdc_df = posData.allData_li[posData.frame_i-1]['acdc_df']
        prev_rp = posData.allData_li[posData.frame_i-1]['regionprops']
        prev_cca_df = prev_acdc_df[self.cca_df_colnames].copy()

        ScellsIDsGone = []
        for ccSeries in prev_cca_df.itertuples():
            ID = ccSeries.Index
            ccs = ccSeries.cell_cycle_stage
            if ccs != 'S':
                continue

            relID = ccSeries.relative_ID
            if relID == -1:
                continue
            
            # Check is relID is gone while ID stays
            if relID not in posData.IDs and ID in posData.IDs:
                ScellsIDsGone.append(relID)

        if not ScellsIDsGone:
            # No cells in S that disappears --> do nothing
            return False, automaticallyDividedIDs

        self.highlightNewIDs_ccaFailed(ScellsIDsGone, rp=prev_rp)
        proceed = self.warnScellsGone(ScellsIDsGone, posData.frame_i)
        self.ax1_lostObjScatterItem.setData([], [])
        if not proceed:
            return True, automaticallyDividedIDs

        for IDgone in ScellsIDsGone:
            relID = prev_cca_df.at[IDgone, 'relative_ID']
            self.annotateDivision(prev_cca_df, IDgone, relID)
            self.annotateDivisionCurrentFrameRelativeIDgone(relID)
            automaticallyDividedIDs.append(relID)
            
        self.store_cca_df(frame_i=posData.frame_i-1, cca_df=prev_cca_df)

        return False, automaticallyDividedIDs

    def annotateDivisionCurrentFrameRelativeIDgone(self, IDwhoseRelativeIsGone):
        posData = self.data[self.pos_i]
        if posData.cca_df is None:
            return
        ID = IDwhoseRelativeIsGone
        posData.cca_df.at[ID, 'generation_num'] += 1
        posData.cca_df.at[ID, 'division_frame_i'] = posData.frame_i-1
        posData.cca_df.at[ID, 'relationship'] = 'mother'

    @exception_handler
    def attempt_auto_cca(self, enforceAll=False):
        posData = self.data[self.pos_i]
        notEnoughG1Cells, proceed = self.autoCca_df(
            enforceAll=enforceAll
        )
        if not proceed:
            return notEnoughG1Cells, proceed
        mode = str(self.modeComboBox.currentText())
        if posData.cca_df is None or mode.find('Cell cycle') == -1:
            notEnoughG1Cells = False
            proceed = True
            return notEnoughG1Cells, proceed
        if posData.cca_df.isna().any(axis=None):
            raise ValueError('Cell cycle analysis table contains NaNs')
        self.checkMultiBudMoth()
        return notEnoughG1Cells, proceed

    def highlightIDs(self, IDs, pen):
        pass

    def warnFrameNeverVisitedSegmMode(self):
        msg = widgets.myMessageBox()
        warn_cca = msg.critical(
            self, 'Next frame NEVER visited',
            'Next frame was never visited in "Segmentation and Tracking"'
            'mode.\n You cannot perform cell cycle analysis on frames'
            'where segmentation and/or tracking errors were not'
            'checked/corrected.\n\n'
            'Switch to "Segmentation and Tracking" mode '
            'and check/correct next frame,\n'
            'before attempting cell cycle analysis again',
        )
        return False

    def checkCcaPastFramesNewIDs(self):
        posData = self.data[self.pos_i]
        if not posData.new_IDs:
            return
        
        found_cca_df_IDs = []
        for frame_i in range(posData.frame_i-2, -1, -1):
            acdc_df = posData.allData_li[frame_i]['acdc_df']
            cca_df_i = acdc_df[self.cca_df_colnames]
            intersect_idx = cca_df_i.index.intersection(posData.new_IDs)
            cca_df_i = cca_df_i.loc[intersect_idx]
            if cca_df_i.empty:
                continue
            found_cca_df_IDs.append(cca_df_i)
            
            # Remove IDs found in past frames from new_IDs list
            newIDs = np.array(posData.new_IDs, dtype=np.uint32)
            mask_index = np.in1d(newIDs, cca_df_i.index)
            posData.new_IDs = list(newIDs[~mask_index])
            if not posData.new_IDs:
                return found_cca_df_IDs
        return found_cca_df_IDs
    
    def initMissingFramesCca(self, last_cca_frame_i, current_frame_i):
        self.logger.info(
            'Initialising cell cycle annotations of missing past frames...'
        )
        posData = self.data[self.pos_i]
        current_frame_i = posData.frame_i
        
        annotated_cca_dfs = []
        for frame_i in range(last_cca_frame_i+1):
            acdc_df = posData.allData_li[frame_i]['acdc_df']
            if 'cell_cycle_stage' in acdc_df.columns:
                continue
            
            acdc_df[self.cca_df_colnames] = ''
        
        annotated_cca_dfs = [
            posData.allData_li[i]['acdc_df'][self.cca_df_colnames]
            for i in range(last_cca_frame_i+1)
        ]
        keys = range(last_cca_frame_i+1)
        names = ['frame_i', 'Cell_ID']
        annotated_cca_df = (
            pd.concat(annotated_cca_dfs, keys=keys, names=names)
            .reset_index()
            .set_index(['Cell_ID', 'frame_i'])
            .sort_index()
        )
        
        last_annotated_cca_df = annotated_cca_df.groupby(level=0).last()
        cca_df_colnames = self.cca_df_colnames
        pbar = tqdm(total=current_frame_i-last_cca_frame_i+1, ncols=100)
        for frame_i in range(last_cca_frame_i, current_frame_i+1):
            posData.frame_i = frame_i
            self.get_data()
            cca_df = self.getBaseCca_df()

            idx = last_annotated_cca_df.index.intersection(cca_df.index)
            cca_df.loc[idx, cca_df_colnames] = last_annotated_cca_df.loc[idx]

            self.store_cca_df(cca_df=cca_df, frame_i=frame_i, autosave=False)
            pbar.update()
        pbar.close()

        posData.frame_i = current_frame_i
        self.get_data()

    def autoCca_df(self, enforceAll=False):
        """
        Assign each bud to a mother with scipy linear sum assignment
        (Hungarian or Munkres algorithm). First we build a cost matrix where
        each (i, j) element is the minimum distance between bud i and mother j.
        Then we minimize the cost of assigning each bud to a mother, and finally
        we write the assignment info into cca_df
        """
        proceed = True
        notEnoughG1Cells = False
        ScellsGone = False

        posData = self.data[self.pos_i]

        # Skip cca if not the right mode
        mode = str(self.modeComboBox.currentText())
        if mode.find('Cell cycle') == -1:
            return notEnoughG1Cells, proceed


        # Make sure that this is a visited frame in segmentation tracking mode
        if posData.allData_li[posData.frame_i]['labels'] is None:
            proceed = self.warnFrameNeverVisitedSegmMode()
            return notEnoughG1Cells, proceed

        # Determine if this is the last visited frame for repeating
        # bud assignment on non manually corrected_assignment buds.
        # The idea is that the user could have assigned division on a cell
        # by going previous and we want to check if this cell could be a
        # "better" mother for those non manually corrected buds
        lastVisited = False
        curr_df = posData.allData_li[posData.frame_i]['acdc_df']
        if curr_df is not None:
            if 'cell_cycle_stage' in curr_df.columns and not enforceAll:
                posData.new_IDs = [
                    ID for ID in posData.new_IDs
                    if curr_df.at[ID, 'is_history_known']
                    and curr_df.at[ID, 'cell_cycle_stage'] == 'S'
                ]
                if posData.frame_i+1 < posData.SizeT:
                    next_df = posData.allData_li[posData.frame_i+1]['acdc_df']
                    if next_df is None:
                        lastVisited = True
                    else:
                        if 'cell_cycle_stage' not in next_df.columns:
                            lastVisited = True
                else:
                    lastVisited = True

        # Use stored cca_df and do not modify it with automatic stuff
        if posData.cca_df is not None and not enforceAll and not lastVisited:
            return notEnoughG1Cells, proceed

        # Keep only correctedAssignIDs if requested
        # For the last visited frame we perform assignment again only on
        # IDs where we didn't manually correct assignment
        if lastVisited and not enforceAll:
            try:
                correctedAssignIDs = curr_df[curr_df['corrected_assignment']].index
            except Exception as e:
                correctedAssignIDs = []
            posData.new_IDs = [
                ID for ID in posData.new_IDs
                if ID not in correctedAssignIDs
            ]

        # Check if new IDs exist some time in the past
        found_cca_df_IDs = self.checkCcaPastFramesNewIDs()

        # Check if there are some S cells that disappeared
        abort, automaticallyDividedIDs = self.checkScellsGone()
        if abort:
            notEnoughG1Cells = False
            proceed = False
            return notEnoughG1Cells, proceed

        # Get previous dataframe
        acdc_df = posData.allData_li[posData.frame_i-1]['acdc_df']
        prev_cca_df = acdc_df[self.cca_df_colnames].copy()

        if posData.cca_df is None:
            posData.cca_df = prev_cca_df
        else:
            posData.cca_df = curr_df[self.cca_df_colnames].copy()

        # concatenate new IDs found in past frames (before frame_i-1)
        if found_cca_df_IDs is not None:
            cca_df = pd.concat([posData.cca_df, *found_cca_df_IDs])
            unique_idx = ~cca_df.index.duplicated(keep='first')
            posData.cca_df = cca_df[unique_idx]
        

        # If there are no new IDs we are done
        if not posData.new_IDs:
            proceed = True
            self.store_cca_df()
            return notEnoughG1Cells, proceed

        # Get cells in G1 (exclude dead) and check if there are enough cells in G1
        prev_df_G1 = prev_cca_df[prev_cca_df['cell_cycle_stage']=='G1']
        prev_df_G1 = prev_df_G1[~acdc_df.loc[prev_df_G1.index]['is_cell_dead']]
        IDsCellsG1 = set(prev_df_G1.index)
        if lastVisited or enforceAll:
            # If we are repeating auto cca for last visited frame
            # then we also add the cells in G1 that we already know
            # at current frame
            df_G1 = posData.cca_df[posData.cca_df['cell_cycle_stage']=='G1']
            IDsCellsG1.update(df_G1.index)

        # remove cells that disappeared
        IDsCellsG1 = [ID for ID in IDsCellsG1 if ID in posData.IDs]

        numCellsG1 = len(IDsCellsG1)
        numNewCells = len(posData.new_IDs)
        if numCellsG1 < numNewCells:
            self.highlightNewCellNotEnoughG1cells(posData.new_IDs)
            msg = widgets.myMessageBox()
            text = html_utils.paragraph(
                f'In the next frame <b>{numNewCells} new object(s)</b> will '
                'appear (highlighted in green on left image).<br><br>'
                f'However there are <b>only {numCellsG1} object(s)</b> '
                'in G1 available.<br><br>'
                'You can either cancel the operation and "free" a cell '
                'by first annotating division or continue.<br><br>'
                'If you continue the <b>new cell</b> will be annotated as a '
                '<b>cell in G1 with unknown history</b>.<br><br>'
                'Do you want to continue?<br>'
            )
            _, yesButton = msg.warning(
                self, 'No cells in G1!', text,
                buttonsTexts=('Cancel', 'Yes')
            )
            if msg.clickedButton == yesButton:
                notEnoughG1Cells = False
                proceed = True
                # Annotate the new IDs with unknown history
                for ID in posData.new_IDs:
                    posData.cca_df.loc[ID] = pd.Series({
                        'cell_cycle_stage': 'G1',
                        'generation_num': 2,
                        'relative_ID': -1,
                        'relationship': 'mother',
                        'emerg_frame_i': -1,
                        'division_frame_i': -1,
                        'is_history_known': False,
                        'corrected_assignment': False,
                        'will_divide': 0
                    })
                    cca_df_ID = self.getStatusKnownHistoryBud(ID)
                    posData.ccaStatus_whenEmerged[ID] = cca_df_ID
            else:
                notEnoughG1Cells = True
                proceed = False
            self.ccaFailedScatterItem.setData([], [])
            return notEnoughG1Cells, proceed

        # Compute new IDs contours
        newIDs_contours = []
        for obj in posData.rp:
            ID = obj.label
            if ID in posData.new_IDs:
                cont = self.getObjContours(obj)
                newIDs_contours.append(cont)

        # Compute cost matrix
        cost = np.full((numCellsG1, numNewCells), np.inf)
        for obj in posData.rp:
            ID = obj.label
            if ID in IDsCellsG1:
                cont = self.getObjContours(obj)
                i = IDsCellsG1.index(ID)
                for j, newID_cont in enumerate(newIDs_contours):
                    min_dist, nearest_xy = self.nearest_point_2Dyx(
                        cont, newID_cont
                    )
                    cost[i, j] = min_dist

        # Run hungarian (munkres) assignment algorithm
        row_idx, col_idx = scipy.optimize.linear_sum_assignment(cost)

        # Assign buds to mothers
        for i, j in zip(row_idx, col_idx):
            mothID = IDsCellsG1[i]
            budID = posData.new_IDs[j]

            # If we are repeating assignment for the bud then we also have to
            # correct the possibily wrong mother first
            if budID in posData.cca_df.index:
                relID = posData.cca_df.at[budID, 'relative_ID']
                if relID in prev_cca_df.index:
                    posData.cca_df.loc[relID] = prev_cca_df.loc[relID]


            posData.cca_df.at[mothID, 'relative_ID'] = budID
            posData.cca_df.at[mothID, 'cell_cycle_stage'] = 'S'

            posData.cca_df.loc[budID] = pd.Series({
                'cell_cycle_stage': 'S',
                'generation_num': 0,
                'relative_ID': mothID,
                'relationship': 'bud',
                'emerg_frame_i': posData.frame_i,
                'division_frame_i': -1,
                'is_history_known': True,
                'corrected_assignment': False,
                'will_divide': 0
            })


        # Keep only existing IDs
        posData.cca_df = posData.cca_df.loc[posData.IDs]

        self.store_cca_df()
        proceed = True
        return notEnoughG1Cells, proceed

    def getObjBbox(self, obj_bbox):
        if self.isSegm3D and len(obj_bbox)==6:
            obj_bbox = (obj_bbox[1], obj_bbox[2], obj_bbox[4], obj_bbox[5])
            return obj_bbox
        else:
            return obj_bbox

    def z_lab(self, checkIfProj=False):
        if checkIfProj and self.zProjComboBox.currentText() != 'single z-slice':
            return
        
        if self.isSegm3D:
            return self.zSliceScrollBar.sliderPosition()
        else:
            return

    def get_2Dlab(self, lab, force_z=True):
        if self.isSegm3D:
            if force_z:
                return lab[self.z_lab()]
            zProjHow = self.zProjComboBox.currentText()
            isZslice = zProjHow == 'single z-slice'
            if isZslice:
                return lab[self.z_lab()]
            else:
                return lab.max(axis=0)
        else:
            return lab

    # @exec_time
    def applyEraserMask(self, mask):
        posData = self.data[self.pos_i]
        if self.isSegm3D:
            zProjHow = self.zProjComboBox.currentText()
            isZslice = zProjHow == 'single z-slice'
            if isZslice:
                posData.lab[self.z_lab(), mask] = 0
            else:
                posData.lab[:, mask] = 0
        else:
            posData.lab[mask] = 0
    
    def changeBrushID(self):
        """Function called when pressing or releasing shift
        """        
        if not self.isSegm3D:
            # Changing brush ID with shift is only for 3D segm
            return

        if not self.brushButton.isChecked():
            # Brush if not active
            return
        
        if not self.isMouseDragImg2 and not self.isMouseDragImg1:
            # Mouse is not brushing at the moment
            return

        posData = self.data[self.pos_i]
        forceNewObj = not self.isNewID
        
        if forceNewObj:
            # Shift is down --> force new object with brush
            # e.g., 24 --> 28: 
            # 24 is hovering ID that we store as self.prevBrushID
            # 24 object becomes 28 that is the new posData.brushID
            self.isNewID = True
            self.changedID = posData.brushID
            self.restoreBrushID = posData.brushID
            # Set a new ID
            self.setBrushID()
            self.ax2BrushID = posData.brushID
        else:
            # Shift released or hovering on ID in z+-1 
            # --> restore brush ID from before shift was pressed or from 
            # when we started brushing from outside an object 
            # but we hovered on ID in z+-1 while dragging.
            # We change the entire 28 object to 24 so before changing the 
            # brush ID back to 24 we builg the mask with 28 to change it to 24
            self.isNewID = False
            self.changedID = posData.brushID
            # Restore ID   
            posData.brushID = self.restoreBrushID
            self.ax2BrushID = self.restoreBrushID
               
        brushID = posData.brushID
        brushIDmask = self.get_2Dlab(posData.lab) == self.changedID
        self.applyBrushMask(brushIDmask, brushID)
        if self.isMouseDragImg1:
            self.brushColor = self.lut[posData.brushID]/255
            self.setTempImg1Brush(True, brushIDmask, posData.brushID)

    def applyBrushMask(self, mask, ID, toLocalSlice=None):
        posData = self.data[self.pos_i]
        if self.isSegm3D:
            zProjHow = self.zProjComboBox.currentText()
            isZslice = zProjHow == 'single z-slice'
            if isZslice:
                if toLocalSlice is not None:
                    toLocalSlice = (self.z_lab(), *toLocalSlice)
                    posData.lab[toLocalSlice][mask] = ID
                else:
                    posData.lab[self.z_lab()][mask] = ID
            else:
                if toLocalSlice is not None:
                    for z in range(len(posData.lab)):
                        _slice = (z, *toLocalSlice)
                        posData.lab[_slice][mask] = ID
                else:
                    posData.lab[:, mask] = ID
        else:
            if toLocalSlice is not None:
                posData.lab[toLocalSlice][mask] = ID
            else:
                posData.lab[mask] = ID

    def get_2Drp(self, lab=None):
        if self.isSegm3D:
            if lab is None:
                # self.currentLab2D is defined at self.setImageImg2()
                lab = self.currentLab2D
            lab = self.get_2Dlab(lab)
            rp = skimage.measure.regionprops(lab)
            return rp
        else:
            return self.data[self.pos_i].rp

    def set_2Dlab(self, lab2D):
        posData = self.data[self.pos_i]
        if self.isSegm3D:
            zProjHow = self.zProjComboBox.currentText()
            isZslice = zProjHow == 'single z-slice'
            if isZslice:
                posData.lab[self.z_lab()] = lab2D
            else:
                posData.lab[:] = lab2D
        else:
            posData.lab = lab2D

    def get_labels(self, is_stored=False, frame_i=None, return_existing=False):
        posData = self.data[self.pos_i]
        if frame_i is None:
            frame_i = posData.frame_i
        existing = True
        if is_stored:
            labels = posData.allData_li[frame_i]['labels'].copy()
        else:
            try:
                labels = posData.segm_data[frame_i].copy()
            except IndexError:
                existing = False
                # Visting a frame that was not segmented --> empty masks
                if self.isSegm3D:
                    shape = (posData.SizeZ, posData.SizeY, posData.SizeX)
                else:
                    shape = (posData.SizeY, posData.SizeX)
                labels = np.zeros(shape, dtype=np.uint32)
        if return_existing:
            return labels, existing
        else:
            return labels

    def _get_editID_info(self, df):
        if 'was_manually_edited' not in df.columns:
            return []
        manually_edited_df = df[df['was_manually_edited'] > 0]
        editID_info = [
            (row.y_centroid, row.x_centroid, row.Index)
            for row in manually_edited_df.itertuples()
        ]
        return editID_info

    @get_data_exception_handler
    def get_data(self, debug=False):
        posData = self.data[self.pos_i]
        proceed_cca = True
        if posData.frame_i > 2:
            # Remove undo states from 4 frames back to avoid memory issues
            posData.UndoRedoStates[posData.frame_i-4] = []
            # Check if current frame contains undo states (not empty list)
            if posData.UndoRedoStates[posData.frame_i]:
                self.undoAction.setDisabled(False)
            elif posData.UndoRedoCcaStates[posData.frame_i]:
                self.undoAction.setDisabled(False)
            else:
                self.undoAction.setDisabled(True)
        self.UndoCount = 0
        # If stored labels is None then it is the first time we visit this frame
        if posData.allData_li[posData.frame_i]['labels'] is None:
            posData.editID_info = []
            never_visited = True
            if str(self.modeComboBox.currentText()) == 'Cell cycle analysis':
                # Warn that we are visiting a frame that was never segm-checked
                # on cell cycle analysis mode
                msg = widgets.myMessageBox()
                txt = html_utils.paragraph(
                    'Segmentation and Tracking was <b>never checked from '
                    f'frame {posData.frame_i+1} onwards</b>.<br><br>'
                    'To ensure correct cell cell cycle analysis you have to '
                    'first visit the frames after '
                    f'{posData.frame_i+1} with "Segmentation and Tracking" mode.'
                )
                warn_cca = msg.critical(
                    self, 'Never checked segmentation on requested frame', txt                    
                )
                proceed_cca = False
                return proceed_cca, never_visited
            # Requested frame was never visited before. Load from HDD
            posData.lab = self.get_labels()
            posData.rp = skimage.measure.regionprops(posData.lab)
            if posData.acdc_df is not None:
                frames = posData.acdc_df.index.get_level_values(0)
                if posData.frame_i in frames:
                    # Since there was already segmentation metadata from
                    # previous closed session add it to current metadata
                    df = posData.acdc_df.loc[posData.frame_i].copy()
                    binnedIDs_df = df[df['is_cell_excluded']>0]
                    binnedIDs = set(binnedIDs_df.index).union(posData.binnedIDs)
                    posData.binnedIDs = binnedIDs
                    ripIDs_df = df[df['is_cell_dead']>0]
                    ripIDs = set(ripIDs_df.index).union(posData.ripIDs)
                    posData.ripIDs = ripIDs
                    posData.editID_info.extend(self._get_editID_info(df))
                    # Load cca df into current metadata
                    if 'cell_cycle_stage' in df.columns:
                        if any(df['cell_cycle_stage'].isna()):
                            if 'is_history_known' not in df.columns:
                                df['is_history_known'] = True
                            if 'corrected_assignment' not in df.columns:
                                df['corrected_assignment'] = True
                            df = df.drop(labels=self.cca_df_colnames, axis=1)
                        else:
                            # Convert to ints since there were NaN
                            cols = self.cca_df_int_cols
                            df[cols] = df[cols].astype(int)
                    i = posData.frame_i
                    posData.allData_li[i]['acdc_df'] = df.copy()
            self.get_cca_df()
        else:
            # Requested frame was already visited. Load from RAM.
            never_visited = False
            posData.lab = self.get_labels(is_stored=True)
            posData.rp = skimage.measure.regionprops(posData.lab)
            df = posData.allData_li[posData.frame_i]['acdc_df']
            binnedIDs_df = df[df['is_cell_excluded']>0]
            posData.binnedIDs = set(binnedIDs_df.index)
            ripIDs_df = df[df['is_cell_dead']>0]
            posData.ripIDs = set(ripIDs_df.index)
            posData.editID_info = self._get_editID_info(df)
            self.get_cca_df()

        self.update_rp_metadata(draw=False)
        posData.IDs = [obj.label for obj in posData.rp]
        posData.IDs_idxs = {
            ID:i for ID, i in zip(posData.IDs, range(len(posData.IDs)))
        }
        self.pointsLayerDfsToData(posData)
        return proceed_cca, never_visited

    def load_delROIs_info(self, delROIshapes, last_tracked_num):
        posData = self.data[self.pos_i]
        delROIsInfo_npz = posData.delROIsInfo_npz
        if delROIsInfo_npz is None:
            return
        for file in posData.delROIsInfo_npz.files:
            if not file.startswith(f'{posData.frame_i}_'):
                continue

            delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
            if file.startswith(f'{posData.frame_i}_delMask'):
                delMask = delROIsInfo_npz[file]
                delROIs_info['delMasks'].append(delMask)
            elif file.startswith(f'{posData.frame_i}_delIDs'):
                delIDsROI = set(delROIsInfo_npz[file])
                delROIs_info['delIDsROI'].append(delIDsROI)
            elif file.startswith(f'{posData.frame_i}_roi'):
                Y, X = self.get_2Dlab(posData.lab).shape
                x0, y0, w, h = delROIsInfo_npz[file]
                addROI = (
                    posData.frame_i==0 or
                    [x0, y0, w, h] not in delROIshapes[posData.frame_i]
                )
                if addROI:
                    roi = self.getDelROI(xl=x0, yb=y0, w=w, h=h)
                    for i in range(posData.frame_i, last_tracked_num):
                        delROIs_info_i = posData.allData_li[i]['delROIs_info']
                        delROIs_info_i['rois'].append(roi)
                        delROIshapes[i].append([x0, y0, w, h])

    def addIDBaseCca_df(self, posData, ID):
        if ID <= 0:
            # When calling update_cca_df_deletedIDs we add relative IDs
            # but they could be -1 for cells in G1
            return

        _zip = zip(
            self.cca_df_colnames,
            self.cca_df_default_values,
        )
        if posData.cca_df.empty:
            posData.cca_df = pd.DataFrame(
                {col: val for col, val in _zip},
                index=[ID]
            )
        else:
            for col, val in _zip:
                posData.cca_df.at[ID, col] = val
        self.store_cca_df()

    def getBaseCca_df(self):
        posData = self.data[self.pos_i]
        IDs = [obj.label for obj in posData.rp]
        cc_stage = ['G1' for ID in IDs]
        num_cycles = [2]*len(IDs)
        relationship = ['mother' for ID in IDs]
        related_to = [-1]*len(IDs)
        emerg_frame_i = [-1]*len(IDs)
        division_frame_i = [-1]*len(IDs)
        is_history_known = [False]*len(IDs)
        corrected_assignment = [False]*len(IDs)
        will_divide = [0]*len(IDs)
        cca_df = pd.DataFrame({
            'cell_cycle_stage': cc_stage,
            'generation_num': num_cycles,
            'relative_ID': related_to,
            'relationship': relationship,
            'emerg_frame_i': emerg_frame_i,
            'division_frame_i': division_frame_i,
            'is_history_known': is_history_known,
            'corrected_assignment': corrected_assignment,
            'will_divide': will_divide
            },
            index=IDs
        )
        cca_df.index.name = 'Cell_ID'
        return cca_df
    
    def get_last_tracked_i(self):
        posData = self.data[self.pos_i]
        last_tracked_i = 0
        for frame_i, data_dict in enumerate(posData.allData_li):
            lab = data_dict['labels']
            if lab is None and frame_i == 0:
                last_tracked_i = 0
                break
            elif lab is None:
                last_tracked_i = frame_i-1
                break
            else:
                last_tracked_i = posData.segmSizeT-1
        return last_tracked_i

    def initSegmTrackMode(self):
        posData = self.data[self.pos_i]
        last_tracked_i = self.get_last_tracked_i()

        if posData.frame_i > last_tracked_i:
            # Prompt user to go to last tracked frame
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph(
                f'The last visited frame in "Segmentation and Tracking mode" '
                f'is frame {last_tracked_i+1}.\n\n'
                f'We recommend to resume from that frame.<br><br>'
                'How do you want to proceed?'
            )
            goToButton, stayButton = msg.warning(
                self, 'Go to last visited frame?', txt,
                buttonsTexts=(
                    f'Resume from frame {last_tracked_i+1} (RECOMMENDED)',
                    f'Stay on current frame {posData.frame_i+1}'
                )
            )
            if msg.clickedButton == goToButton:
                posData.frame_i = last_tracked_i
                self.get_data()
                self.updateAllImages(updateFilters=True)
                self.updateScrollbars()
            else:
                last_tracked_i = posData.frame_i
                current_frame_i = posData.frame_i
                self.logger.info(
                    f'Storing data up until frame n. {current_frame_i+1}...'
                )
                pbar = tqdm(total=current_frame_i+1, ncols=100)
                for i in range(current_frame_i):
                    posData.frame_i = i
                    self.get_data()
                    self.store_data(autosave=i==current_frame_i-1)
                    pbar.update()
                pbar.close()

                posData.frame_i = current_frame_i
                self.get_data()
        
        self.navigateScrollBar.setMaximum(last_tracked_i+1)
        self.navSpinBox.setMaximum(last_tracked_i+1)

        self.checkTrackingEnabled()

    @exception_handler
    def initCca(self):
        posData = self.data[self.pos_i]
        last_tracked_i = self.get_last_tracked_i()
        defaultMode = 'Viewer'
        if last_tracked_i == 0:
            txt = html_utils.paragraph(
                'On this dataset either you <b>never checked</b> that the segmentation '
                'and tracking are <b>correct</b> or you did not save yet.<br><br>'
                'If you already visited some frames with "Segmentation and tracking" '
                'mode save data before switching to "Cell cycle analysis mode".<br><br>'
                'Otherwise you first have to check (and eventually correct) some frames '
                'in "Segmentation and Tracking" mode before proceeding '
                'with cell cycle analysis.')
            msg = widgets.myMessageBox()
            msg.critical(
                self, 'Tracking was never checked', txt
            )
            self.modeComboBox.setCurrentText(defaultMode)
            return

        proceed = True
        i = 0
        # Determine last annotated frame index
        for i, dict_frame_i in enumerate(posData.allData_li):
            df = dict_frame_i['acdc_df']
            if df is None:
                break
            elif 'cell_cycle_stage' not in df.columns:
                break
        
        last_cca_frame_i = i if i==0 or i+1==len(posData.allData_li) else i-1

        if last_cca_frame_i == 0:
            # Remove undoable actions from segmentation mode
            posData.UndoRedoStates[0] = []
            self.undoAction.setEnabled(False)
            self.redoAction.setEnabled(False)

        if posData.frame_i > last_cca_frame_i:
            # Prompt user to go to last annotated frame
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph(f"""
                The <b>last annotated frame</b> is frame {last_cca_frame_i+1}.<br><br>
                Do you want to restart cell cycle analysis from frame 
                {last_cca_frame_i+1}?<br>
            """)
            _, yesButton, stayButton = msg.warning(
                self, 'Go to last annotated frame?', txt, 
                buttonsTexts=(
                    'Cancel', f'Yes, go to frame {last_cca_frame_i+1}', 
                    'No, stay on current frame')
            )
            if yesButton == msg.clickedButton:
                msg = 'Looking good!'
                self.last_cca_frame_i = last_cca_frame_i
                posData.frame_i = last_cca_frame_i
                self.titleLabel.setText(msg, color=self.titleColor)
                self.get_data()
                self.updateAllImages(updateFilters=True)
                self.updateScrollbars()
            elif stayButton == msg.clickedButton:
                self.initMissingFramesCca(last_cca_frame_i, posData.frame_i)
                last_cca_frame_i = posData.frame_i
                msg = 'Cell cycle analysis initialised!'
                self.titleLabel.setText(msg, color='g')
            elif msg.cancel:
                msg = 'Cell cycle analysis aborted.'
                self.logger.info(msg)
                self.titleLabel.setText(msg, color=self.titleColor)
                self.modeComboBox.setCurrentText(defaultMode)
                proceed = False
                return
        elif posData.frame_i < last_cca_frame_i:
            # Prompt user to go to last annotated frame
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph(f"""
                The <b>last annotated frame</b> is frame {last_cca_frame_i+1}.<br><br>
                Do you want to restart cell cycle analysis from frame
                {last_cca_frame_i+1}?<br>
            """)
            goTo_last_annotated_frame_i = msg.question(
                self, 'Go to last annotated frame?', txt, 
                buttonsTexts=('Yes', 'No', 'Cancel')
            )[0]
            if goTo_last_annotated_frame_i == msg.clickedButton:
                msg = 'Looking good!'
                self.titleLabel.setText(msg, color=self.titleColor)
                self.last_cca_frame_i = last_cca_frame_i
                posData.frame_i = last_cca_frame_i
                self.get_data()
                self.updateAllImages(updateFilters=True)
                self.updateScrollbars()
            elif msg.cancel:
                msg = 'Cell cycle analysis aborted.'
                self.logger.info(msg)
                self.titleLabel.setText(msg, color=self.titleColor)
                self.modeComboBox.setCurrentText(defaultMode)
                proceed = False
                return
        else:
            self.get_data()

        self.last_cca_frame_i = last_cca_frame_i

        self.navigateScrollBar.setMaximum(last_cca_frame_i+1)
        self.navSpinBox.setMaximum(last_cca_frame_i+1)

        if posData.cca_df is None:
            posData.cca_df = self.getBaseCca_df()
            self.store_cca_df()
            msg = 'Cell cycle analysis initialized!'
            self.logger.info(msg)
            self.titleLabel.setText(msg, color=self.titleColor)
        else:
            self.get_cca_df()
        return proceed

    def remove_future_cca_df(self, from_frame_i):
        posData = self.data[self.pos_i]
        self.last_cca_frame_i = posData.frame_i
        self.setNavigateScrollBarMaximum()
        for i in range(from_frame_i, posData.SizeT):
            df = posData.allData_li[i]['acdc_df']
            if df is None:
                # No more saved info to delete
                return

            if 'cell_cycle_stage' not in df.columns:
                # No cell cycle info present
                continue

            df.drop(self.cca_df_colnames, axis=1, inplace=True)
            posData.allData_li[i]['acdc_df'] = df

    def get_cca_df(self, frame_i=None, return_df=False):
        # cca_df is None unless the metadata contains cell cycle annotations
        # NOTE: cell cycle annotations are either from the current session
        # or loaded from HDD in "initPosAttr" with a .question to the user
        posData = self.data[self.pos_i]
        cca_df = None
        i = posData.frame_i if frame_i is None else frame_i
        df = posData.allData_li[i]['acdc_df']
        if df is not None:
            if 'cell_cycle_stage' in df.columns:
                if 'is_history_known' not in df.columns:
                    df['is_history_known'] = True
                if 'corrected_assignment' not in df.columns:
                    # Compatibility with those acdc_df analysed with prev vers.
                    df['corrected_assignment'] = True
                cca_df = df[self.cca_df_colnames].copy()
        if cca_df is None and self.isSnapshot:
            cca_df = self.getBaseCca_df()
            posData.cca_df = cca_df
        if return_df:
            return cca_df
        else:
            posData.cca_df = cca_df

    def unstore_cca_df(self):
        posData = self.data[self.pos_i]
        acdc_df = posData.allData_li[posData.frame_i]['acdc_df']
        for col in self.cca_df_colnames:
            if col not in acdc_df.columns:
                continue
            acdc_df.drop(col, axis=1, inplace=True)

    def store_cca_df(
            self, pos_i=None, frame_i=None, cca_df=None, mainThread=True,
            autosave=True
        ):
        pos_i = self.pos_i if pos_i is None else pos_i
        posData = self.data[pos_i]
        i = posData.frame_i if frame_i is None else frame_i
        if cca_df is None:
            cca_df = posData.cca_df
            if self.ccaTableWin is not None and mainThread:
                self.ccaTableWin.updateTable(posData.cca_df)

        acdc_df = posData.allData_li[i]['acdc_df']
        if acdc_df is None:
            self.store_data()
            acdc_df = posData.allData_li[i]['acdc_df']
        if 'cell_cycle_stage' in acdc_df.columns:
            # Cell cycle info already present --> overwrite with new
            df = acdc_df
            df[self.cca_df_colnames] = cca_df
            posData.allData_li[i]['acdc_df'] = df.copy()
        elif cca_df is not None:
            df = acdc_df.join(cca_df, how='left')
            posData.allData_li[i]['acdc_df'] = df.copy()
        
        if autosave:
            self.enqAutosave()
    
    def turnOffAutoSaveWorker(self):
        self.autoSaveToggle.setChecked(False)
    
    def enqAutosave(self):
        posData = self.data[self.pos_i]  
        # if self.autoSaveToggle.isChecked():
        if not self.autoSaveActiveWorkers:
            self.gui_createAutoSaveWorker()
        
        worker, thread = self.autoSaveActiveWorkers[-1]
        self.statusBarLabel.setText('Autosaving...')
        worker.enqueue(posData)
    
    def drawAllMothBudLines(self):
        posData = self.data[self.pos_i]
        for obj in posData.rp:
            self.drawObjMothBudLines(obj, posData, ax=0)
            self.drawObjMothBudLines(obj, posData, ax=1)
    
    def drawObjMothBudLines(self, obj, posData, ax=0):
        if not self.areMothBudLinesRequested(ax):
            return
        
        if posData.cca_df is None:
            return 

        ID = obj.label
        try:
            cca_df_ID = posData.cca_df.loc[ID]
        except KeyError:
            return        
        
        isObjVisible = self.isObjVisible(obj.bbox)
        if not isObjVisible:
            return
        
        ccs_ID = cca_df_ID['cell_cycle_stage']
        if ccs_ID == 'G1':
            return

        relationship = cca_df_ID['relationship']
        if relationship != 'bud':
            return

        emerg_frame_i = cca_df_ID['emerg_frame_i']
        isNew = emerg_frame_i == posData.frame_i
        scatterItem = self.getMothBudLineScatterItem(ax, isNew)
        relative_ID = cca_df_ID['relative_ID']

        try:
            relative_rp_idx = posData.IDs_idxs[relative_ID]
        except KeyError:
            return

        relative_ID_obj = posData.rp[relative_rp_idx]
        y1, x1 = self.getObjCentroid(obj.centroid)
        y2, x2 = self.getObjCentroid(relative_ID_obj.centroid)
        xx, yy = core.get_line(y1, x1, y2, x2, dashed=True)
        scatterItem.addPoints(xx, yy)

    def getObjCentroid(self, obj_centroid):
        if self.isSegm3D:
            return obj_centroid[1:3]
        else:
            return obj_centroid
    
    def getAnnotateHowRightImage(self):
        if not self.labelsGrad.showRightImgAction.isChecked():
            return 'nothing'
        
        if self.rightBottomGroupbox.isChecked():
            how = self.annotateRightHowCombobox.currentText()
        else:
            how = self.drawIDsContComboBox.currentText()
        return how

    def getObjOptsSegmLabels(self, obj):
        if not self.labelsGrad.showLabelsImgAction.isChecked():
            return

        objOpts = self.getObjTextAnnotOpts(obj, 'Draw only IDs', ax=1)
        return objOpts

    @exception_handler
    def update_rp(self, draw=True, debug=False):
        posData = self.data[self.pos_i]
        # Update rp for current posData.lab (e.g. after any change)
        posData.rp = skimage.measure.regionprops(posData.lab)
        posData.IDs = [obj.label for obj in posData.rp]
        posData.IDs_idxs = {
            ID:i for ID, i in zip(posData.IDs, range(len(posData.IDs)))
        }
        self.update_rp_metadata(draw=draw)

    def extendLabelsLUT(self, lenNewLut):
        posData = self.data[self.pos_i]
        # Build a new lut to include IDs > than original len of lut
        if lenNewLut > len(self.lut):
            numNewColors = lenNewLut-len(self.lut)
            # Index original lut
            _lut = np.zeros((lenNewLut, 3), np.uint8)
            _lut[:len(self.lut)] = self.lut
            # Pick random colors and append them at the end to recycle them
            randomIdx = np.random.randint(0,len(self.lut),size=numNewColors)
            for i, idx in enumerate(randomIdx):
                rgb = self.lut[idx]
                _lut[len(self.lut)+i] = rgb
            self.lut = _lut
            self.initLabelsImageItems()
            return True
        return False

    def initLookupTableLab(self):
        self.img2.setLookupTable(self.lut)
        self.img2.setLevels([0, len(self.lut)])
        self.initLabelsImageItems()
    
    def initLabelsImageItems(self):
        lut = np.zeros((len(self.lut), 4), dtype=np.uint8)
        lut[:,-1] = 255
        lut[:,:-1] = self.lut
        lut[0] = [0,0,0,0]
        self.labelsLayerImg1.setLevels([0, len(lut)])
        self.labelsLayerRightImg.setLevels([0, len(lut)])
        self.labelsLayerImg1.setLookupTable(lut)
        self.labelsLayerRightImg.setLookupTable(lut)
        alpha = self.imgGrad.labelsAlphaSlider.value()
        self.labelsLayerImg1.setOpacity(alpha)
        self.labelsLayerRightImg.setOpacity(alpha)
    
    def initKeepObjLabelsLayers(self):
        lut = np.zeros((len(self.lut), 4), dtype=np.uint8)
        lut[:,:-1] = self.lut
        lut[:,-1:] = 255
        lut[0] = [0,0,0,0]
        self.keepIDsTempLayerLeft.setLevels([0, len(lut)])
        self.keepIDsTempLayerLeft.setLookupTable(lut)

        # Gray out objects
        alpha = self.imgGrad.labelsAlphaSlider.value()
        self.labelsLayerImg1.setOpacity(alpha/3)
        self.labelsLayerRightImg.setOpacity(alpha/3)

        # Gray out contours
        imageItem = self.getContoursImageItem(0)
        if imageItem is not None:
            imageItem.setOpacity(0.3)
        
        imageItem = self.getContoursImageItem(1)
        if imageItem is not None:
            imageItem.setOpacity(0.3)
        
    
    def updateTempLayerKeepIDs(self):
        if not self.keepIDsButton.isChecked():
            return
        
        keptLab = np.zeros_like(self.currentLab2D)

        posData = self.data[self.pos_i]
        for obj in posData.rp:
            if obj.label not in self.keptObjectsIDs:
                continue

            if not self.isObjVisible(obj.bbox):
                continue

            _slice = self.getObjSlice(obj.slice)
            _objMask = self.getObjImage(obj.image, obj.bbox)

            keptLab[_slice][_objMask] = obj.label

        self.keepIDsTempLayerLeft.setImage(keptLab, autoLevels=False)

    def highlightLabelID(self, ID, ax=0):        
        posData = self.data[self.pos_i]
        obj = posData.rp[posData.IDs_idxs[ID]]
        self.textAnnot[ax].highlightObject(obj)
    
    def _keepObjects(self, keepIDs=None, lab=None, rp=None):
        posData = self.data[self.pos_i]
        if lab is None:
            lab = posData.lab
        
        if rp is None:
            rp = posData.rp
        
        if keepIDs is None:
            keepIDs = self.keptObjectsIDs

        for obj in rp:
            if obj.label in keepIDs:
                continue

            lab[obj.slice][obj.image] = 0
        
        return lab
    
    def clearHighlightedText(self):
        pass
    
    def updateKeepIDs(self, IDs):
        posData = self.data[self.pos_i]

        self.clearHighlightedText()

        isAnyIDnotExisting = False
        # Check if IDs from line edit are present in current keptObjectIDs list
        for ID in IDs:
            if ID not in posData.allIDs:
                isAnyIDnotExisting = True
                continue
            if ID not in self.keptObjectsIDs:
                self.keptObjectsIDs.append(ID, editText=False)
                self.highlightLabelID(ID)
        
        # Check if IDs in current keptObjectsIDs are present in IDs from line edit
        for ID in self.keptObjectsIDs:
            if ID not in posData.allIDs:
                isAnyIDnotExisting = True
                continue
            if ID not in IDs:
                self.keptObjectsIDs.remove(ID, editText=False)
        
        self.updateTempLayerKeepIDs()
        if isAnyIDnotExisting:
            self.keptIDsLineEdit.warnNotExistingID()
        else:
            self.keptIDsLineEdit.setInstructionsText()
    
    @exception_handler
    def applyKeepObjects(self):
        # Store undo state before modifying stuff
        self.storeUndoRedoStates(False)

        self._keepObjects()
        self.highlightHoverIDsKeptObj(0, 0, hoverID=0)
        
        posData = self.data[self.pos_i]

        self.update_rp()
        # Repeat tracking
        self.tracking(enforce=True, assign_unique_new_IDs=False)

        if self.isSnapshot:
            self.fixCcaDfAfterEdit('Deleted non-selected objects')
            self.updateAllImages()
            self.keptObjectsIDs = widgets.KeptObjectIDsList(
                self.keptIDsLineEdit, self.keepIDsConfirmAction
            )
            return
        else:
            removeAnnot = self.warnEditingWithCca_df(
                'Deleted non-selected objects', get_answer=True
            )
            if not removeAnnot:
                # We can propagate changes only if the user agrees on 
                # removing annotations
                return
        
        self.current_frame_i = posData.frame_i
        if posData.frame_i > 0:
            txt = html_utils.paragraph("""
                Do you want to <b>remove un-kept objects in the past</b> frames too?
            """)
            msg = widgets.myMessageBox(wrapText=False, showCentered=False)
            _, _, applyToPastButton = msg.question(
                self, 'Propagate to past frames?', txt,
                buttonsTexts=('Cancel', 'No', 'Yes, apply to past frames')
            )
            if msg.cancel:
                return
            if msg.clickedButton == applyToPastButton:
                self.store_data()
                self.logger.info('Applying keep objects to past frames...')
                for i in tqdm(range(posData.frame_i), ncols=100):
                    lab = posData.allData_li[i]['labels']
                    rp = posData.allData_li[i]['regionprops']
                    keepLab = self._keepObjects(lab=lab, rp=rp)
                    # Store change
                    posData.allData_li[i]['labels'] = keepLab.copy()
                    # Get the rest of the stored metadata based on the new lab
                    posData.frame_i = i
                    self.get_data()
                    if not removeAnnot and posData.cca_df is not None:
                        delIDs = [
                            ID for ID in posData.cca_df.index 
                            if ID not in posData.IDs
                        ]
                        self.update_cca_df_deletedIDs(posData, delIDs)
                    self.store_data(autosave=False)
                
                posData.frame_i = self.current_frame_i
                self.get_data()

        # Ask to propagate change to all future visited frames
        (UndoFutFrames, applyFutFrames, endFrame_i,
        doNotShowAgain) = self.propagateChange(
            self.keptObjectsIDs, 'Keep ID', posData.doNotShowAgain_keepID,
            posData.UndoFutFrames_keepID, posData.applyFutFrames_keepID,
            force=True, applyTrackingB=True
        )

        if UndoFutFrames is None:
            # Empty keep object list
            self.keptObjectsIDs = widgets.KeptObjectIDsList(
                self.keptIDsLineEdit, self.keepIDsConfirmAction
            )
            return

        posData.doNotShowAgain_keepID = doNotShowAgain
        posData.UndoFutFrames_keepID = UndoFutFrames
        posData.applyFutFrames_keepID = applyFutFrames
        includeUnvisited = posData.includeUnvisitedInfo['Keep ID']

        if applyFutFrames:
            self.store_data()

            self.logger.info('Applying to future frames...')
            pbar = tqdm(total=posData.SizeT-posData.frame_i-1, ncols=100)
            segmSizeT = len(posData.segm_data)
            for i in range(posData.frame_i+1, segmSizeT):
                lab = posData.allData_li[i]['labels']
                if lab is None and not includeUnvisited:
                    self.enqAutosave()
                    pbar.update(posData.SizeT-i)
                    break
                
                rp = posData.allData_li[i]['regionprops']

                if lab is not None:
                    keepLab = self._keepObjects(lab=lab, rp=rp)

                    # Store change
                    posData.allData_li[i]['labels'] = keepLab.copy()
                    # Get the rest of the stored metadata based on the new lab
                    posData.frame_i = i
                    self.get_data()
                    if not removeAnnot and posData.cca_df is not None:
                        delIDs = [
                            ID for ID in posData.cca_df.index 
                            if ID not in posData.IDs
                        ]
                        self.update_cca_df_deletedIDs(posData, delIDs)
                    self.store_data(autosave=False)
                elif includeUnvisited:
                    # Unvisited frame (includeUnvisited = True)
                    lab = posData.segm_data[i]
                    rp = skimage.measure.regionprops(lab)
                    keepLab = self._keepObjects(lab=lab, rp=rp)
                    posData.segm_data[i] = keepLab
                
                pbar.update()
            pbar.close()
        
        # Back to current frame
        if applyFutFrames:
            posData.frame_i = self.current_frame_i
            self.get_data()

        self.keptObjectsIDs = widgets.KeptObjectIDsList(
            self.keptIDsLineEdit, self.keepIDsConfirmAction
        )

    def updateLookuptable(self, lenNewLut=None, delIDs=None):
        posData = self.data[self.pos_i]
        if lenNewLut is None:
            try:
                if delIDs is None:
                    IDs = posData.IDs
                else:
                    # Remove IDs removed with ROI from LUT
                    IDs = [ID for ID in posData.IDs if ID not in delIDs]
                lenNewLut = max(IDs, default=0) + 1
            except ValueError:
                # Empty segmentation mask
                lenNewLut = 1
        # Build a new lut to include IDs > than original len of lut
        updateLevels = self.extendLabelsLUT(lenNewLut)
        lut = self.lut.copy()

        try:
            # lut = self.lut[:lenNewLut].copy()
            for ID in posData.binnedIDs:
                lut[ID] = lut[ID]*0.2

            for ID in posData.ripIDs:
                lut[ID] = lut[ID]*0.2
        except Exception as e:
            err_str = traceback.format_exc()
            print('='*30)
            self.logger.info(err_str)
            print('='*30)

        if updateLevels:
            self.img2.setLevels([0, len(lut)])
        
        if self.keepIDsButton.isChecked():
            lut = np.round(lut*0.3).astype(np.uint8)
            keptLut = np.round(lut[self.keptObjectsIDs]/0.3).astype(np.uint8)
            lut[self.keptObjectsIDs] = keptLut

        self.img2.setLookupTable(lut)

    def update_rp_metadata(self, draw=True):
        posData = self.data[self.pos_i]
        # Add to rp dynamic metadata (e.g. cells annotated as dead)
        for i, obj in enumerate(posData.rp):
            ID = obj.label
            obj.excluded = ID in posData.binnedIDs
            obj.dead = ID in posData.ripIDs
    
    def annotate_rip_and_bin_IDs(self, updateLabel=False):
        posData = self.data[self.pos_i]
        binnedIDs_xx = []
        binnedIDs_yy = []
        ripIDs_xx = []
        ripIDs_yy = []
        for obj in posData.rp:
            obj.excluded = obj.label in posData.binnedIDs
            obj.dead = obj.label in posData.ripIDs
            if not self.isObjVisible(obj.bbox):
                continue
            
            if obj.excluded:
                y, x = self.getObjCentroid(obj.centroid)
                binnedIDs_xx.append(x)
                binnedIDs_yy.append(y)
                if updateLabel:
                    self.getObjOptsSegmLabels(obj)
                    how = self.drawIDsContComboBox.currentText()
            
            if obj.dead:
                y, x = self.getObjCentroid(obj.centroid)
                ripIDs_xx.append(x)
                ripIDs_yy.append(y)
                if updateLabel:
                    self.getObjOptsSegmLabels(obj)
                    how = self.drawIDsContComboBox.currentText()
        
        self.ax2_binnedIDs_ScatterPlot.setData(binnedIDs_xx, binnedIDs_yy)
        self.ax2_ripIDs_ScatterPlot.setData(ripIDs_xx, ripIDs_yy)
        self.ax1_binnedIDs_ScatterPlot.setData(binnedIDs_xx, binnedIDs_yy)
        self.ax1_ripIDs_ScatterPlot.setData(ripIDs_xx, ripIDs_yy)

    def loadNonAlignedFluoChannel(self, fluo_path):
        posData = self.data[self.pos_i]
        if posData.filename.find('aligned') != -1:
            filename, _ = os.path.splitext(os.path.basename(fluo_path))
            path = f'.../{posData.pos_foldername}/Images/{filename}_aligned.npz'
            msg = widgets.myMessageBox()
            msg.critical(
                self, 'Aligned fluo channel not found!',
                'Aligned data for fluorescence channel not found!\n\n'
                f'You loaded aligned data for the cells channel, therefore '
                'loading NON-aligned fluorescence data is not allowed.\n\n'
                'Run the script "dataPrep.py" to create the following file:\n\n'
                f'{path}'
            )
            return None
        fluo_data = np.squeeze(skimage.io.imread(fluo_path))
        return fluo_data

    def load_fluo_data(self, fluo_path):
        self.logger.info(f'Loading fluorescence image data from "{fluo_path}"...')
        bkgrData = None
        posData = self.data[self.pos_i]
        # Load overlay frames and align if needed
        filename = os.path.basename(fluo_path)
        filename_noEXT, ext = os.path.splitext(filename)
        if ext == '.npy' or ext == '.npz':
            fluo_data = np.load(fluo_path)
            try:
                fluo_data = np.squeeze(fluo_data['arr_0'])
            except Exception as e:
                fluo_data = np.squeeze(fluo_data)

            # Load background data
            bkgrData_path = os.path.join(
                posData.images_path, f'{filename_noEXT}_bkgrRoiData.npz'
            )
            if os.path.exists(bkgrData_path):
                bkgrData = np.load(bkgrData_path)
        elif ext == '.tif' or ext == '.tiff':
            aligned_filename = f'{filename_noEXT}_aligned.npz'
            aligned_path = os.path.join(posData.images_path, aligned_filename)
            if os.path.exists(aligned_path):
                fluo_data = np.load(aligned_path)['arr_0']

                # Load background data
                bkgrData_path = os.path.join(
                    posData.images_path, f'{aligned_filename}_bkgrRoiData.npz'
                )
                if os.path.exists(bkgrData_path):
                    bkgrData = np.load(bkgrData_path)
            else:
                fluo_data = self.loadNonAlignedFluoChannel(fluo_path)
                if fluo_data is None:
                    return None, None

                # Load background data
                bkgrData_path = os.path.join(
                    posData.images_path, f'{filename_noEXT}_bkgrRoiData.npz'
                )
                if os.path.exists(bkgrData_path):
                    bkgrData = np.load(bkgrData_path)
        else:
            txt = html_utils.paragraph(
                f'File format {ext} is not supported!\n'
                'Choose either .tif or .npz files.'
            )
            msg = widgets.myMessageBox()
            msg.critical(self, 'File not supported', txt)
            return None, None

        return fluo_data, bkgrData

    def setOverlayColors(self):
        self.overlayRGBs = [
            (255, 255, 0),
            (252, 72, 254),
            (49, 222, 134),
            (22, 108, 27)
        ]
        cmap = matplotlib.colormaps['hsv']
        self.overlayRGBs.extend(
            [tuple([round(c*255) for c in cmap(i)][:3]) 
            for i in np.linspace(0,1,8)]
        )

    def getFileExtensions(self, images_path):
        alignedFound = any([f.find('_aligned.np')!=-1
                            for f in myutils.listdir(images_path)])
        if alignedFound:
            extensions = (
                'Aligned channels (*npz *npy);; Tif channels(*tiff *tif)'
                ';;All Files (*)'
            )
        else:
            extensions = (
                'Tif channels(*tiff *tif);; All Files (*)'
            )
        return extensions

    def loadOverlayData(self, ol_channels, addToExisting=False):
        posData = self.data[self.pos_i]
        for ol_ch in ol_channels:
            if ol_ch not in list(posData.loadedFluoChannels):
                # Requested channel was never loaded --> load it at first
                # iter i == 0
                success = self.loadFluo_cb(fluo_channels=[ol_ch])
                if not success:
                    return False

        lastChannelName = ol_channels[-1]
        for action in self.fluoDataChNameActions:
            if action.text() == lastChannelName:
                action.setChecked(True)

        for p, posData in enumerate(self.data):
            if addToExisting:
                ol_data = posData.ol_data
            else:
                ol_data = {}
            for i, ol_ch in enumerate(ol_channels):
                _, filename = self.getPathFromChName(ol_ch, posData)
                ol_data[filename] = posData.ol_data_dict[filename].copy()                                  
                self.addFluoChNameContextMenuAction(ol_ch)
            posData.ol_data = ol_data

        return True

    def askSelectOverlayChannel(self):
        ch_names = [ch for ch in self.ch_names if ch != self.user_ch_name]
        selectFluo = widgets.QDialogListbox(
            'Select channel',
            'Select channel names to overlay:\n',
            ch_names, multiSelection=True, parent=self
        )
        selectFluo.exec_()
        if selectFluo.cancel:
            return

        return selectFluo.selectedItemsText
    
    def overlayLabels_cb(self, checked):
        if checked:
            if not self.drawModeOverlayLabelsChannels:
                selectedLabelsEndnames = self.askLabelsToOverlay()
                if selectedLabelsEndnames is None:
                    self.logger.info('Overlay labels cancelled.')
                    self.overlayLabelsButton.setChecked(False)
                    return
                for selectedEndname in selectedLabelsEndnames:
                    self.loadOverlayLabelsData(selectedEndname)
                    for action in self.overlayLabelsContextMenu.actions():
                        if not action.isCheckable():
                            continue
                        if action.text() == selectedEndname:
                            action.setChecked(True)
                lastSelectedName = selectedLabelsEndnames[-1]
                for action in self.selectOverlayLabelsActionGroup.actions():
                    if action.text() == lastSelectedName:
                        action.setChecked(True)
            self.updateAllImages()
        else:
            self.clearOverlayLabelsItems()
            self.setOverlayLabelsItemsVisible(False)
    
    def askLabelsToOverlay(self):
        selectOverlayLabels = widgets.QDialogListbox(
            'Select segmentation to overlay',
            'Select segmentation file to overlay:\n',
            self.existingSegmEndNames, multiSelection=True, parent=self
        )
        selectOverlayLabels.exec_()
        if selectOverlayLabels.cancel:
            return

        return selectOverlayLabels.selectedItemsText

    def closeToolbars(self):
        for toolbar in self.sender().toolbars:
            toolbar.setVisible(False)
            for action in toolbar.actions():
                try:
                    action.button.setChecked(False)
                except Exception as e:
                    pass
    
    def addPointsLayer_cb(self):
        self.pointsLayersToolbar.setVisible(True)
        self.autoPilotZoomToObjToolbar.setVisible(True)
        posData = self.data[self.pos_i]
        self.addPointsWin = apps.AddPointsLayerDialog(
            channelNames=posData.chNames, imagesPath=posData.images_path, 
            parent=self
        )
        cmap = matplotlib.colormaps['gist_rainbow']
        i = np.random.default_rng().uniform()
        for action in self.pointsLayersToolbar.actions()[1:]:
            if not hasattr(action, 'layerTypeIdx'):
                continue
            rgb = [round(c*255) for c in cmap(i)][:3]
            self.addPointsWin.appearanceGroupbox.colorButton.setColor(rgb)
            break
        self.addPointsWin.sigCriticalReadTable.connect(self.logger.info)
        self.addPointsWin.sigLoadedTable.connect(self.logger.info)
        self.addPointsWin.sigClosed.connect(self.addPointsLayer)
        self.addPointsWin.sigCheckClickEntryTableEndnameExists.connect(
            self.checkClickEntryTableEndnameExists
        )
        self.addPointsWin.show()
    
    def buttonAddPointsByClickingActive(self):
        for action in self.pointsLayersToolbar.actions()[1:]:
            if not hasattr(action, 'layerTypeIdx'):
                continue
            if action.layerTypeIdx == 4 and action.button.isChecked():
                return action.button
    
    def setupAddPointsByClicking(self, toolButton, isLoadedDf):
        self.LeftClickButtons.append(toolButton)
        posData = self.data[self.pos_i]
        tableEndName = self.addPointsWin.clickEntryTableEndnameText
        if isLoadedDf is not None:
            posData = self.data[self.pos_i]
            tableEndName = tableEndName[len(posData.basename):]
        toolButton.clickEntryTableEndName = tableEndName
        
        toolButton.toggled.connect(self.addPointsByClickingButtonToggled)
        self.addPointsByClickingButtonToggled(sender=toolButton)
        
        saveAction = QAction(
            QIcon(":file-save.svg"), 
            "Save annotated points in the CSV file ending with 'tableEndName.csv'", 
            self
        )
        saveAction.triggered.connect(self.savePointsAddedByClicking)
        saveAction.toolButton = toolButton
        self.pointsLayersToolbar.addAction(saveAction)
        self.pointsLayerDfsToData(posData)
    
    def autoPilotZoomToObjToggled(self, checked):
        if not checked:
            self.zoomOut()
            return
        
        posData = self.data[self.pos_i]
        if not posData.IDs:
            self.logger.info('There are no objects in current segmentation mask')
            return
        self.autoPilotZoomToObjSpinBox.setValue(posData.IDs[0])
        self.zoomToObj(posData.rp[0])
    
    def savePointsAddedByClickingFromEndname(self, tableEndName):
        self.pointsLayerDataToDf(self.data[self.pos_i])
        for posData in self.data:
            if not posData.basename.endswith('_'):
                basename = f'{posData.basename}_'
            else:
                basename = posData.basename
            tableFilename = f'{basename}{tableEndName}.csv'
            tableFilepath = os.path.join(posData.images_path, tableFilename)
            df = posData.clickEntryPointsDfs.get(tableEndName)
            if df is None:
                continue
            df = df.sort_values(['frame_i', 'Cell_ID'])
            df.to_csv(tableFilepath, index=False)
    
    @exception_handler
    def savePointsAddedByClicking(self):
        toolButton = self.sender().toolButton
        tableEndName = toolButton.clickEntryTableEndName
        
        self.logger.info(f'Saving _{tableEndName}.csv table...')
        
        self.savePointsAddedByClickingFromEndname(tableEndName)
        
        self.logger.info(f'{tableEndName}.csv saved!')
        self.titleLabel.setText(f'{tableEndName}.csv saved!', color='g')
    
    def pointsLayerDfsToData(self, posData):
        for action in self.pointsLayersToolbar.actions()[1:]:
            if not hasattr(action, 'button'):
                continue
            if not hasattr(action.button, 'clickEntryTableEndName'):
                continue
            tableEndName = action.button.clickEntryTableEndName
            action.pointsData = {}
            if posData.clickEntryPointsDfs.get(tableEndName) is None:
                continue
            
            df = posData.clickEntryPointsDfs[tableEndName]
            if self.isSegm3D and df['z'].isna().any():
                self.warnLoadedPointsTableIsNot3D(tableEndName)
                return
            
            for frame_i, df_frame in df.groupby('frame_i'):
                action.pointsData[frame_i] = {}
                if self.isSegm3D:
                    for z, df_zlice in df_frame.groupby('z'):
                        xx = df_zlice['x'].to_list()
                        yy = df_zlice['y'].to_list()
                        action.pointsData[frame_i][z] = {'x': xx, 'y': yy}
                else:
                    xx = df_frame['x'].to_list()
                    yy = df_frame['y'].to_list()
                    action.pointsData[frame_i][z] = {'x': xx, 'y': yy}
            
    def pointsLayerDataToDf(self, posData):
        for action in self.pointsLayersToolbar.actions()[1:]:
            if not hasattr(action, 'button'):
                continue
            if not hasattr(action.button, 'clickEntryTableEndName'):
                continue
            tableEndName = action.button.clickEntryTableEndName
            # if posData.clickEntryPointsDfs.get(tableEndName) is None:
            #     continue
            
            df = pd.DataFrame(columns=['frame_i', 'Cell_ID', 'z', 'y', 'x'])
            frames_vals = []
            IDs = []
            zz = []
            yy = []
            xx = []
            for frame_i, framePointsData in action.pointsData.items():
                if self.isSegm3D:
                    for z, zSlicePointsData in framePointsData.items():
                        yyxx = zip(zSlicePointsData['y'], zSlicePointsData['x'])
                        for y, x in yyxx:
                            ID = posData.lab[int(z), int(y), int(x)]
                            frames_vals.append(frame_i)
                            IDs.append(ID)
                            zz.append(z)
                            yy.append(y)
                            xx.append(x)
                else:
                    yyxx = zip(framePointsData['y'], framePointsData['x'])
                    for y, x in yyxx:
                        ID = posData.lab[int(y), int(x)]
                        frames_vals.append(frame_i)
                        IDs.append(ID)
                        yy.append(y)
                        xx.append(x)
            df['frame_i'] = frames_vals
            df['Cell_ID'] = IDs
            df['y'] = yy
            df['x'] = xx
            if zz:
                df['z'] = zz
            posData.clickEntryPointsDfs[tableEndName] = df
    
    def restartZoomAutoPilot(self):
        if not self.autoPilotZoomToObjToggle.isChecked():
            return
        
        posData = self.data[self.pos_i]
        if not posData.IDs:
            return
        
        self.autoPilotZoomToObjSpinBox.setValue(posData.IDs[0])
        self.zoomToObj(posData.rp[0])
    
    def zoomToObj(self, obj=None):
        if not hasattr(self, 'data'):
            return
        posData = self.data[self.pos_i]
        if obj is None:
            ID = self.sender().value()
            try:
                ID_idx = posData.IDs_idxs[ID]
                obj = obj = posData.rp[ID_idx]
            except Exception as e:
                self.logger.info(
                    f'[WARNING]: ID {ID} does not exist (add points by clicking)'
                )
        
        self.goToZsliceSearchedID(obj)  
        min_row, min_col, max_row, max_col = self.getObjBbox(obj.bbox)
        xRange = min_col-5, max_col+5
        yRange = max_row+5, min_row-5

        self.ax1.setRange(xRange=xRange, yRange=yRange)
        
    def addPointsByClickingButtonToggled(self, checked=True, sender=None):
        if sender is None:
            sender = self.sender()
        if not sender.isChecked():
            action = sender.action
            action.scatterItem.setVisible(False)
            return
        self.disconnectLeftClickButtons()
        self.uncheckLeftClickButtons(sender)
        self.connectLeftClickButtons()
        action = sender.action
        action.scatterItem.setVisible(True)
    
    def autoZoomNextObj(self):
        self.sender().setValue(self.sender().value() - 1)
        self.pointsLayerAutoPilot('next')
        self.setFocusMain()
        self.setFocusGraphics()
    
    def autoZoomPrevObj(self):
        self.sender().setValue(self.sender().value() + 1)
        self.pointsLayerAutoPilot('prev')
        self.setFocusMain()
        self.setFocusGraphics()
    
    def pointsLayerAutoPilot(self, direction):
        if not self.autoPilotZoomToObjToggle.isChecked():
            return
        ID = self.autoPilotZoomToObjSpinBox.value()
        posData = self.data[self.pos_i]
        if not posData.IDs:
            return
        
        try:
            ID_idx = posData.IDs_idxs[ID]
            if direction == 'next':
                nextID_idx = ID_idx + 1
            else:
                nextID_idx = ID_idx - 1
            obj = posData.rp[nextID_idx]
        except Exception as e:
            # printl(traceback.format_exc())
            self.logger.info(
                f'Auto-pilot restarted from first ID'
            )
            obj = posData.rp[0]
        
        self.autoPilotZoomToObjSpinBox.setValue(obj.label)
        self.zoomToObj(obj)        
        
    def checkClickEntryTableEndnameExists(self, tableEndName, forceLoading=False):
        doesTableExists = False
        for posData in self.data:
            files = myutils.listdir(posData.images_path)
            for file in files:
                if file.endswith(f'{tableEndName}.csv'):
                    doesTableExists = True
                    break
        
        if not doesTableExists:
            return
        
        if not forceLoading:
            msg = widgets.myMessageBox(wrapText=False)
            txt = html_utils.paragraph(
                f'The table <code>{tableEndName}.csv</code> already exists!<br><br>'
                'Do you want to load it?'
            )
            _, yesButton, _ = msg.warning(
                self.addPointsWin, 'Table exists!', txt,
                buttonsTexts=('Cancel', 'Yes, load it', 'No, let me enter a new name')
            )
            if msg.clickedButton != yesButton:
                return

        self.loadClickEntryDfs(tableEndName)

    def addPointsLayer(self):
        if self.addPointsWin.cancel:
            self.logger.info('Adding points layer cancelled.')
            return
        
        symbol = self.addPointsWin.symbol
        color = self.addPointsWin.color
        pointSize = self.addPointsWin.pointSize
        r,g,b,a = color.getRgb()

        scatterItem = pg.ScatterPlotItem(
            [], [], symbol=symbol, pxMode=False, size=pointSize,
            brush=pg.mkBrush(color=(r,g,b,100)),
            pen=pg.mkPen(width=2, color=(r,g,b)),
            hoverable=True, hoverBrush=pg.mkBrush((r,g,b,200)), 
            tip=None
        )
        self.ax1.addItem(scatterItem)

        toolButton = widgets.PointsLayerToolButton(symbol, color, parent=self)
        toolTip = (
            f'"{self.addPointsWin.layerType}" points layer\n\n'
            f'SHORTCUT: "{self.addPointsWin.shortcut}"'
        )
        if hasattr(self.addPointsWin, 'description'):
            toolTip = f'{toolTip}\nDescription: {self.addPointsWin.description}'
        toolButton.setToolTip(toolTip)
        toolButton.setCheckable(True)
        toolButton.setChecked(True)
        if self.addPointsWin.keySequence is not None:
            toolButton.setShortcut(self.addPointsWin.keySequence)
        toolButton.toggled.connect(self.pointLayerToolbuttonToggled)
        toolButton.sigEditAppearance.connect(self.editPointsLayerAppearance)
        
        action = self.pointsLayersToolbar.addWidget(toolButton)
        action.state = self.addPointsWin.state()

        toolButton.action = action
        action.button = toolButton
        action.scatterItem = scatterItem
        action.layerType = self.addPointsWin.layerType
        action.layerTypeIdx = self.addPointsWin.layerTypeIdx
        action.pointsData = self.addPointsWin.pointsData
        
        if self.addPointsWin.layerType.startswith('Click to annotate point'):
            isLoadedDf = self.addPointsWin.clickEntryIsLoadedDf
            self.setupAddPointsByClicking(toolButton, isLoadedDf)
            if self.addPointsWin.autoPilotToggle.isChecked():
                self.autoPilotZoomToObjToggle.setChecked(True)

        weighingChannel = self.addPointsWin.weighingChannel
        self.loadPointsLayerWeighingData(action, weighingChannel)

        self.drawPointsLayers()
    
    def loadClickEntryDfs(self, tableEndName):
        for posData in self.data:
            if posData.basename.endswith('_'):
                basename = posData.basename
            else:
                basename = f'{posData.basename}_'
            csv_filename = f'{basename}{tableEndName}'
            if not csv_filename.endswith('.csv'):
                csv_filename = f'{csv_filename}.csv'
            filepath = os.path.join(posData.images_path, csv_filename)
            if not os.path.exists(filepath):
                continue
            posData.clickEntryPointsDfs[tableEndName] = pd.read_csv(filepath)
    
    def removeClickedPoints(self, action, points):
        posData = self.data[self.pos_i]
        framePointsData = action.pointsData[posData.frame_i]
        if self.isSegm3D:
            zSlice = self.z_lab()
        else:
            zSlice = None
        for point in points:
            pos = point.pos()
            x, y = pos.x(), pos.y()
            if zSlice is not None:
                framePointsData[zSlice]['x'].remove(x)
                framePointsData[zSlice]['y'].remove(y)
            else:
                framePointsData['x'].remove(x)
                framePointsData['y'].remove(y)
    
    def addClickedPoint(self, action, x, y):
        x, y = round(x, 2), round(y, 2)
        posData = self.data[self.pos_i]
        framePointsData = action.pointsData.get(posData.frame_i)
        if framePointsData is None:
            if self.isSegm3D:
                zSlice = self.z_lab()
                action.pointsData[posData.frame_i] = {
                    zSlice: {'x': [x], 'y': [y]}
                }
            else:
                action.pointsData[posData.frame_i] = {'x': [x], 'y': [y]}
        else:
            if self.isSegm3D:
                zSlice = self.z_lab()
                z_data = action.pointsData[posData.frame_i].get(zSlice)
                if z_data is None:
                    framePointsData[zSlice] = {'x': [x], 'y': [y]}
                else:
                    framePointsData[zSlice]['x'].append(x)
                    framePointsData[zSlice]['y'].append(y)
                action.pointsData[posData.frame_i] = framePointsData
    
    def editPointsLayerAppearance(self, button):
        win = apps.EditPointsLayerAppearanceDialog(parent=self)
        win.restoreState(button.action.state)
        win.exec_()
        if win.cancel:
            return
        
        symbol = win.symbol
        color = win.color
        pointSize = win.pointSize
        r,g,b,a = color.getRgb()

        scatterItem = button.action.scatterItem
        scatterItem.opts['hoverBrush'] = pg.mkBrush((r,g,b,200))
        scatterItem.setSymbol(symbol, update=False)
        scatterItem.setBrush(pg.mkBrush(color=(r,g,b,100)), update=False)
        scatterItem.setPen(pg.mkPen(width=2, color=(r,g,b)), update=False)
        scatterItem.setSize(pointSize, update=True)

        button.action.state = win.state()
    
    def loadPointsLayerWeighingData(self, action, weighingChannel):
        if not weighingChannel:
            return
        
        self.logger.info(f'Loading "{weighingChannel}" weighing data...')
        action.weighingData = []
        for p, posData in enumerate(self.data):
            if weighingChannel == posData.user_ch_name:
                wData = posData.img_data
                action.weighingData.append(wData)
                continue

            path, filename = self.getPathFromChName(weighingChannel, posData)
            if path is None:
                self.criticalFluoChannelNotFound(weighingChannel, posData) 
                action.weighingData = []
                return
            
            if filename in posData.fluo_data_dict:
                # Weighing data already loaded as additional fluo channel
                wData = posData.fluo_data_dict[filename]
            else:
                # Weighing data never loaded --> load now
                wData, _ = self.load_fluo_data(path)
                if posData.SizeT == 1:
                    wData = wData[np.newaxis]
            action.weighingData.append(wData)

    def pointLayerToolbuttonToggled(self, checked):
        action = self.sender().action
        action.scatterItem.setVisible(checked)
    
    def getCentroidsPointsData(self, action):
        # Centroids (either weighted or not)
        # NOTE: if user requested to draw from table we load that in 
        # apps.AddPointsLayerDialog.ok_cb()
        posData = self.data[self.pos_i]
        action.pointsData[posData.frame_i] = {}
        if hasattr(action, 'weighingData'):
            lab = posData.lab
            img = action.weighingData[self.pos_i][posData.frame_i]
            rp = skimage.measure.regionprops(lab, intensity_image=img)
            attr = 'weighted_centroid'
        else:
            rp = posData.rp
            attr = 'centroid'
        for i, obj in enumerate(rp):
            centroid = getattr(obj, attr)
            if len(centroid) == 3:
                zc, yc, xc = centroid
                z_int = round(zc)
                if z_int not in action.pointsData[posData.frame_i]:
                    action.pointsData[posData.frame_i][z_int] = {
                        'x': [xc], 'y': [yc]
                    }
                else:
                    z_data = action.pointsData[posData.frame_i][z_int]
                    z_data['x'].append(xc)
                    z_data['y'].append(yc)
            else:
                yc, xc = centroid
                if 'y' not in action.pointsData[posData.frame_i]:
                    action.pointsData[posData.frame_i]['y'] = [yc]
                    action.pointsData[posData.frame_i]['x'] = [xc]
                else:
                    action.pointsData[posData.frame_i]['y'].append(yc)
                    action.pointsData[posData.frame_i]['x'].append(xc)
    
    def drawPointsLayers(self, computePointsLayers=True):
        posData = self.data[self.pos_i]
        for action in self.pointsLayersToolbar.actions()[1:]:
            if not hasattr(action, 'layerTypeIdx'):
                continue
            if action.layerTypeIdx < 2 and computePointsLayers:
                self.getCentroidsPointsData(action)

            if not action.button.isChecked():
                continue
            
            # printl(action.pointsData, action.layerTypeIdx)
            if posData.frame_i not in action.pointsData:
                if action.layerTypeIdx != 4:
                    self.logger.info(
                        f'Frame number {posData.frame_i+1} does not have any '
                        f'"{action.layerType}" point to display.'
                    )
                continue
            
            if self.isSegm3D:
                zProjHow = self.zProjComboBox.currentText()
                isZslice = zProjHow == 'single z-slice'
                if isZslice:
                    zSlice = self.z_lab()
                    z_data = action.pointsData[posData.frame_i].get(zSlice)
                    if z_data is None:
                        # There are no objects on this z-slice
                        action.scatterItem.clear()
                        return
                    xx, yy = z_data['x'], z_data['y']
                else:
                    xx, yy = [], []
                    # z-projection --> draw all points
                    for z, z_data in action.pointsData[posData.frame_i].items():
                        xx.extend(z_data['x'])
                        yy.extend(z_data['y'])
            else:
                # 2D segmentation
                xx = action.pointsData[posData.frame_i]['x']
                yy = action.pointsData[posData.frame_i]['y']

            action.scatterItem.setData(xx, yy)

    def overlay_cb(self, checked):
        self.UserNormAction, _, _ = self.getCheckNormAction()
        posData = self.data[self.pos_i]
        if checked:
            self.setRetainSizePolicyLutItems()
            if posData.ol_data is None:
                selectedChannels = self.askSelectOverlayChannel()
                if selectedChannels is None:
                    self.overlayButton.toggled.disconnect()
                    self.overlayButton.setChecked(False)
                    self.overlayButton.toggled.connect(self.overlay_cb)
                    return
                
                success = self.loadOverlayData(selectedChannels)         
                if not success:
                    return False
                lastChannel = selectedChannels[-1]
                self.setCheckedOverlayContextMenusActions(selectedChannels)
                imageItem = self.overlayLayersItems[lastChannel][0]
                self.setOpacityOverlayLayersItems(0.5, imageItem=imageItem)
                self.img1.setOpacity(0.5)

            self.normalizeRescale0to1Action.setChecked(True)

            self.updateAllImages()
            self.updateImageValueFormatter()
            self.enableOverlayWidgets(True)
        else:
            self.img1.setOpacity(1.0)
            self.updateAllImages()
            self.updateImageValueFormatter()
            self.enableOverlayWidgets(False)
            
            for items in self.overlayLayersItems.values():
                imageItem = items[0]
                imageItem.clear()
        
        self.setOverlayItemsVisible()
    
    def showLabelRoiContextMenu(self, event):
        menu = QMenu(self.labelRoiButton)
        action = QAction('Re-initialize magic labeller model...')
        action.triggered.connect(self.initLabelRoiModel)
        menu.addAction(action)
        menu.exec_(QCursor.pos())
    
    def initLabelRoiModel(self):
        self.app.restoreOverrideCursor()
        # Ask which model
        win = apps.QDialogSelectModel(parent=self)
        win.exec_()
        if win.cancel:
            self.logger.info('Magic labeller aborted.')
            return True
        self.app.setOverrideCursor(Qt.WaitCursor)
        model_name = win.selectedModel
        self.labelRoiModel = self.repeatSegm(
            model_name=model_name, askSegmParams=True,
            return_model=True
        )
        if self.labelRoiModel is None:
            return True
        self.labelRoiViewCurrentModelAction.setDisabled(False)
        return False

    def showOverlayContextMenu(self, event):
        if not self.overlayButton.isChecked():
            return

        self.overlayContextMenu.exec_(QCursor.pos())
    
    def showOverlayLabelsContextMenu(self, event):
        if not self.overlayLabelsButton.isChecked():
            return

        self.overlayLabelsContextMenu.exec_(QCursor.pos())

    def showInstructionsCustomModel(self):
        modelFilePath = apps.addCustomModelMessages(self)
        if modelFilePath is None:
            self.logger.info('Adding custom model process stopped.')
            return
        
        myutils.store_custom_model_path(modelFilePath)
        modelName = os.path.basename(os.path.dirname(modelFilePath))
        customModelAction = QAction(modelName)
        self.segmSingleFrameMenu.addAction(customModelAction)
        self.segmActions.append(customModelAction)
        self.segmActionsVideo.append(customModelAction)
        self.modelNames.append(modelName)
        self.models.append(None)
        self.sender().callback(customModelAction)
        
    def setCheckedOverlayContextMenusActions(self, channelNames):
        for action in self.overlayContextMenu.actions():
            if action.text() in channelNames:
                action.setChecked(True)
                self.checkedOverlayChannels.add(action.text())

    def enableOverlayWidgets(self, enabled):
        posData = self.data[self.pos_i]   
        if enabled:
            self.overlayColorButton.setDisabled(False)
            self.editOverlayColorAction.setDisabled(False)

            if posData.SizeZ == 1:
                return

            self.zSliceOverlay_SB.setMaximum(posData.SizeZ-1)
            if self.zProjOverlay_CB.currentText().find('max') != -1:
                self.overlay_z_label.setDisabled(True)
                self.zSliceOverlay_SB.setDisabled(True)
            else:
                z = self.zSliceOverlay_SB.sliderPosition()
                self.overlay_z_label.setText(f'Overlay z-slice  {z+1:02}/{posData.SizeZ}')
                self.zSliceOverlay_SB.setDisabled(False)
                self.overlay_z_label.setDisabled(False)
            self.zSliceOverlay_SB.show()
            self.overlay_z_label.show()
            self.zProjOverlay_CB.show()
            self.zSliceOverlay_SB.valueChanged.connect(self.updateOverlayZslice)
            self.zProjOverlay_CB.currentTextChanged.connect(self.updateOverlayZproj)
            self.zProjOverlay_CB.setCurrentIndex(4)
            self.zProjOverlay_CB.activated.connect(self.clearComboBoxFocus)
        else:
            self.zSliceOverlay_SB.setDisabled(True)
            self.zSliceOverlay_SB.hide()
            self.overlay_z_label.hide()
            self.zProjOverlay_CB.hide()
            self.overlayColorButton.setDisabled(True)
            self.editOverlayColorAction.setDisabled(True)

            if posData.SizeZ == 1:
                return

            self.zSliceOverlay_SB.valueChanged.disconnect()
            self.zProjOverlay_CB.currentTextChanged.disconnect()
            self.zProjOverlay_CB.activated.disconnect()


    def criticalFluoChannelNotFound(self, fluo_ch, posData):
        msg = widgets.myMessageBox(showCentered=False)
        ls = "\n".join(myutils.listdir(posData.images_path))
        msg.setDetailedText(
            f'Files present in the {posData.relPath} folder:\n'
            f'{ls}'
        )
        title = 'Requested channel data not found!'
        txt = html_utils.paragraph(
            f'The folder <code>{posData.pos_path}</code> '
            '<b>does not contain</b> '
            'either one of the following files:<br><br>'
            f'{posData.basename}{fluo_ch}.tif<br>'
            f'{posData.basename}{fluo_ch}_aligned.npz<br><br>'
            'Data loading aborted.'
        )
        msg.addShowInFileManagerButton(posData.images_path)
        okButton = msg.warning(
            self, title, txt, buttonsTexts=('Ok')
        )

    def imgGradLUT_cb(self, LUTitem):
        pass

    def imgGradLUTfinished_cb(self):
        posData = self.data[self.pos_i]
        ticks = self.imgGrad.gradient.listTicks()

        self.img1ChannelGradients[self.user_ch_name] = {
            'ticks': [(x, t.color.getRgb()) for t,x in ticks],
            'mode': 'rgb'
        }
        
        self.df_settings = self.imgGrad.saveState(self.df_settings)
        self.df_settings.to_csv(self.settings_csv_path)

    def updateContColour(self, colorButton):
        color = colorButton.color().getRgb()
        self.df_settings.at['contLineColor', 'value'] = str(color)
        self._updateContColour(color)
        self.updateAllImages()
    
    def _updateContColour(self, color):
        self.gui_createContourPens()
        for items in self.overlayLayersItems.values():
            lutItem = items[1]
            lutItem.contoursColorButton.setColor(color)
        
    def saveContColour(self, colorButton):
        self.df_settings.to_csv(self.settings_csv_path)
    
    def updateMothBudLineColour(self, colorButton):
        color = colorButton.color().getRgb()
        self.df_settings.at['mothBudLineColor', 'value'] = str(color)
        self._updateMothBudLineColour(color)
        self.updateAllImages()
    
    def _updateMothBudLineColour(self, color):
        self.gui_createMothBudLinePens()
        self.ax1_newMothBudLinesItem.setBrush(self.newMothBudLineBrush)
        self.ax1_oldMothBudLinesItem.setBrush(self.oldMothBudLineBrush)
        self.ax2_newMothBudLinesItem.setBrush(self.newMothBudLineBrush)
        self.ax2_oldMothBudLinesItem.setBrush(self.oldMothBudLineBrush)
        for items in self.overlayLayersItems.values():
            lutItem = items[1]
            lutItem.mothBudLineColorButton.setColor(color)

    def saveMothBudLineColour(self, colorButton):
        self.df_settings.to_csv(self.settings_csv_path)

    def contLineWeightToggled(self, checked=True):
        if not checked:
            return
        self.imgGrad.uncheckContLineWeightActions()
        w = self.sender().lineWeight
        self.df_settings.at['contLineWeight', 'value'] = w
        self.df_settings.to_csv(self.settings_csv_path)
        self._updateContLineThickness()
        self.updateAllImages()
    
    def _updateContLineThickness(self):
        self.gui_createContourPens()
        for act in self.imgGrad.contLineWightActionGroup.actions():
            if act == self.sender():
                act.setChecked(True)
            act.toggled.connect(self.contLineWeightToggled)
    
    def mothBudLineWeightToggled(self, checked=True):
        if not checked:
            return
        self.imgGrad.uncheckContLineWeightActions()
        w = self.sender().lineWeight
        self.df_settings.at['mothBudLineSize', 'value'] = w
        self.df_settings.to_csv(self.settings_csv_path)
        self._updateMothBudLineSize(w)
        self.updateAllImages()
    
    def _updateMothBudLineSize(self, size):
        self.gui_createMothBudLinePens()
        
        for act in self.imgGrad.mothBudLineWightActionGroup.actions():
            if act == self.sender():
                act.setChecked(True)
            act.toggled.connect(self.mothBudLineWeightToggled)
        
        self.ax1_oldMothBudLinesItem.setSize(size)
        self.ax1_newMothBudLinesItem.setSize(size)
        self.ax2_oldMothBudLinesItem.setSize(size)
        self.ax2_newMothBudLinesItem.setSize(size)

    def getOlImg(self, key, normalizeIntens=True, frame_i=None):
        posData = self.data[self.pos_i]
        if frame_i is None:
            frame_i = posData.frame_i

        img = posData.ol_data[key][frame_i]
        if posData.SizeZ > 1:
            zProjHow = self.zProjOverlay_CB.currentText()
            z = self.zSliceOverlay_SB.sliderPosition()
            if zProjHow == 'same as above':
                zProjHow = self.zProjComboBox.currentText()
                z = self.zSliceScrollBar.sliderPosition()
                reconnect = False
                try:
                    self.zSliceOverlay_SB.valueChanged.disconnect()
                    reconnect = True
                except TypeError:
                    pass
                self.zSliceOverlay_SB.setSliderPosition(z)
                if reconnect:
                    self.zSliceOverlay_SB.valueChanged.connect(
                        self.updateOverlayZslice
                    )
            if zProjHow == 'single z-slice':
                self.overlay_z_label.setText(f'Overlay z-slice  {z+1:02}/{posData.SizeZ}')
                ol_img = img[z].copy()
            elif zProjHow == 'max z-projection':
                ol_img = img.max(axis=0).copy()
            elif zProjHow == 'mean z-projection':
                ol_img = img.mean(axis=0).copy()
            elif zProjHow == 'median z-proj.':
                ol_img = np.median(img, axis=0).copy()
        else:
            ol_img = img.copy()

        if normalizeIntens:
            ol_img = self.normalizeIntensities(ol_img)
        return ol_img
    
    def setTextAnnotZsliceScrolling(self):
        pass
    
    def setGraphicalAnnotZsliceScrolling(self):
        posData = self.data[self.pos_i]
        if self.isSegm3D:
            self.currentLab2D = posData.lab[self.z_lab()]
            self.setOverlaySegmMasks()
            self.doCustomAnnotation(0)
            self.update_rp_metadata()
        else:
            self.currentLab2D = posData.lab
            self.setOverlaySegmMasks()
        self.updateContoursImage(0)
        self.updateContoursImage(1)

    def initContoursImage(self):
        posData = self.data[self.pos_i]
        if hasattr(posData, 'lab'):
            Y, X = posData.lab.shape[-2:]
        else:
            Y, X = posData.img_data.shape[-2:]
        self.contoursImage = np.zeros((Y, X, 4), dtype=np.uint8)
    
    def initTextAnnot(self, force=False):
        posData = self.data[self.pos_i]
        if hasattr(posData, 'lab'):
            Y, X = posData.lab.shape[-2:]
        else:
            Y, X = posData.img_data.shape[-2:]
        self.textAnnot[0].initItem((Y, X))
        self.textAnnot[1].initItem((Y, X))  
    
    def getObjContours(self, obj, all_external=False, local=False):
        obj_image = self.getObjImage(obj.image, obj.bbox).astype(np.uint8)
        obj_bbox = self.getObjBbox(obj.bbox)
        try:
            contours = core.get_obj_contours(
                obj_image=obj_image, obj_bbox=obj_bbox, local=local,
                all_external=all_external
            )
        except Exception as e:
            if all_external:
                contours = []
            else:
                contours = None
            self.logger.info(
                f'[WARNING]: Object ID {obj.label} contours drawing failed. '
                f'(bounding box = {obj.bbox})'
            )
        return contours
    
    def setOverlaySegmMasks(self, force=False, forceIfNotActive=False):
        if not hasattr(self, 'currentLab2D'):
            return

        how = self.drawIDsContComboBox.currentText()
        isOverlaySegmLeftActive = how.find('overlay segm. masks') != -1

        how_ax2 = self.getAnnotateHowRightImage()
        isOverlaySegmRightActive = (
            how_ax2.find('overlay segm. masks') != -1
            and self.labelsGrad.showRightImgAction.isChecked()
        )

        isOverlaySegmActive = (
            isOverlaySegmLeftActive or isOverlaySegmRightActive
            or force
        )
        if not isOverlaySegmActive and not forceIfNotActive:
            return 

        alpha = self.imgGrad.labelsAlphaSlider.value()
        if alpha == 0:
            return

        posData = self.data[self.pos_i]
        maxID = max(posData.IDs, default=0)

        if maxID >= len(self.lut):
            self.extendLabelsLUT(maxID+10)

        if isOverlaySegmLeftActive:
            self.labelsLayerImg1.setImage(self.currentLab2D, autoLevels=False)

        if isOverlaySegmRightActive: 
            self.labelsLayerRightImg.setImage(self.currentLab2D, autoLevels=False)
    
    def getObject2DimageFromZ(self, z, obj):
        posData = self.data[self.pos_i]
        z_min = obj.bbox[0]
        local_z = z - z_min
        if local_z >= posData.SizeZ or local_z < 0:
            return
        return obj.image[local_z]
    
    def getObject2DsliceFromZ(self, z, obj):
        posData = self.data[self.pos_i]
        z_min = obj.bbox[0]
        local_z = z - z_min
        if local_z >= posData.SizeZ or local_z < 0:
            return
        return obj.image[local_z]

    def isObjVisible(self, obj_bbox, debug=False):
        if self.isSegm3D:
            zProjHow = self.zProjComboBox.currentText()
            isZslice = zProjHow == 'single z-slice'
            if not isZslice:
                # required a projection --> all obj are visible
                return True
            min_z = obj_bbox[0]
            max_z = obj_bbox[3]
            if self.z_lab()>=min_z and self.z_lab()<max_z:
                return True
            else:
                return False
        else:
            return True

    def getObjImage(self, obj_image, obj_bbox):
        if self.isSegm3D and len(obj_bbox)==6:
            zProjHow = self.zProjComboBox.currentText()
            isZslice = zProjHow == 'single z-slice'
            if not isZslice:
                # required a projection
                return obj_image.max(axis=0)

            min_z = obj_bbox[0]
            z = self.z_lab()
            local_z = z - min_z
            return obj_image[local_z]
        else:
            return obj_image

    def getObjSlice(self, obj_slice):
        if self.isSegm3D:
            return obj_slice[1:3]
        else:
            return obj_slice
    
    def setOverlayImages(self, frame_i=None, updateFilters=False):
        posData = self.data[self.pos_i]
        if posData.ol_data is None:
            return
        for filename in posData.ol_data:
            chName = myutils.get_chname_from_basename(
                filename, posData.basename, remove_ext=False
            )
            if chName not in self.checkedOverlayChannels:
                continue
            imageItem = self.overlayLayersItems[chName][0]

            if not updateFilters:
                filteredData = self.filteredData.get(chName)
                if filteredData is None:
                    # Filtered data not existing
                    ol_img = self.getOlImg(filename, frame_i=frame_i)
                elif posData.SizeZ > 1:
                    # 3D filtered data (see self.applyFilter)
                    ol_img = self.get_2Dimg_from_3D(
                        filteredData, isLayer0=False
                    )
                else:
                    # 2D filtered data (see self.applyFilter)
                    ol_img = filteredData
            else:
                ol_img = self.applyFilter(chName, setImg=False)

            imageItem.setImage(ol_img)
        
    def initShortcuts(self):
        from . import config
        cp = config.ConfigParser()
        if os.path.exists(shortcut_filepath):
            cp.read(shortcut_filepath)
        
        if 'keyboard.shortcuts' not in cp:
            cp['keyboard.shortcuts'] = {}
        
        shortcuts = {}
        for name, widget in self.widgetsWithShortcut.items():
            if name not in cp.options('keyboard.shortcuts'):
                if hasattr(widget, 'keyPressShortcut'):
                    key = widget.keyPressShortcut
                    shortcut = QKeySequence(key)
                else:
                    shortcut = widget.shortcut()
                shortcut_text = shortcut.toString()
                cp['keyboard.shortcuts'][name] = shortcut_text
            else:
                shortcut_text = cp['keyboard.shortcuts'][name]
                shortcut = QKeySequence(shortcut_text)
            
            shortcuts[name] = (shortcut_text, shortcut)
        self.setShortcuts(shortcuts, save=False)
        with open(shortcut_filepath, 'w') as ini:
            cp.write(ini)
    
    def setShortcuts(self, shortcuts: dict, save=True):
        for name, (text, shortcut) in shortcuts.items():
            widget = self.widgetsWithShortcut[name]
            if hasattr(widget, 'keyPressShortcut'):
                widget.keyPressShortcut = shortcut
            else:
                widget.setShortcut(shortcut)
            s = widget.toolTip()
            toolTip = re.sub(r'SHORTCUT: "(.*)"', f'SHORTCUT: "{text}"', s)
            widget.setToolTip(toolTip)
        
        if not save: 
            return
        
        from . import config
        cp = config.ConfigParser()
        if os.path.exists(shortcut_filepath):
            cp.read(shortcut_filepath)
        
        if 'keyboard.shortcuts' not in cp:
            cp['keyboard.shortcuts'] = {}

        for name, (text, shortcut) in shortcuts.items():
            cp['keyboard.shortcuts'][name] = text
        
        with open(shortcut_filepath, 'w') as ini:
            cp.write(ini)
    
    def editShortcuts_cb(self):
        win = apps.ShortcutEditorDialog(self.widgetsWithShortcut, parent=self)
        win.exec_()
        if win.cancel:
            return
        self.setShortcuts(win.customShortcuts)
            
    def toggleOverlayColorButton(self, checked=True):
        self.mousePressColorButton(None)

    def toggleTextIDsColorButton(self, checked=True):
        self.textIDsColorButton.selectColor()

    def updateTextAnnotColor(self, button):
        r, g, b = np.array(self.textIDsColorButton.color().getRgb()[:3])
        self.imgGrad.textColorButton.setColor((r, g, b))
        for items in self.overlayLayersItems.values():
            lutItem = items[1]
            lutItem.textColorButton.setColor((r, g, b))
        self.gui_createTextAnnotColors(r,g,b, custom=True)
        self.gui_setTextAnnotColors()
        self.updateAllImages()

    def saveTextIDsColors(self, button):
        self.df_settings.at['textIDsColor', 'value'] = self.objLabelAnnotRgb
        self.df_settings.to_csv(self.settings_csv_path)

    def setLut(self, shuffle=True):
        self.lut = self.labelsGrad.item.colorMap().getLookupTable(0,1,255)     
        if shuffle:
            np.random.shuffle(self.lut)
        
        # Insert background color
        if 'labels_bkgrColor' in self.df_settings.index:
            rgbString = self.df_settings.at['labels_bkgrColor', 'value']
            try:
                r, g, b = rgbString
            except Exception as e:
                r, g, b = colors.rgb_str_to_values(rgbString)
        else:
            r, g, b = 25, 25, 25
            self.df_settings.at['labels_bkgrColor', 'value'] = (r, g, b)

        self.lut = np.insert(self.lut, 0, [r, g, b], axis=0)

    def useCenterBrushCursorHoverIDtoggled(self, checked):
        if checked:
            self.df_settings.at['useCenterBrushCursorHoverID', 'value'] = 'Yes'
        else:
            self.df_settings.at['useCenterBrushCursorHoverID', 'value'] = 'No'
        self.df_settings.to_csv(self.settings_csv_path)

    def shuffle_cmap(self):
        np.random.shuffle(self.lut[1:])
        self.initLabelsImageItems()
        self.updateAllImages()
    
    def greedyShuffleCmap(self):
        lut = self.labelsGrad.item.colorMap().getLookupTable(0,1,255)
        greedy_lut = colors.get_greedy_lut(self.currentLab2D, lut)
        self.lut = greedy_lut
        self.initLabelsImageItems()
        self.updateAllImages()
    
    def highlightZneighLabels_cb(self, checked):
        if checked:
            pass
        else:
            pass
    
    def setTwoImagesLayout(self, isTwoImages):
        if isTwoImages:
            self.graphLayout.removeItem(self.titleLabel)
            self.graphLayout.addItem(self.titleLabel, row=0, col=1, colspan=2)
            # self.mainLayout.setAlignment(self.bottomLayout, Qt.AlignLeft)
            self.ax2.show()
            self.ax2.vb.setYLink(self.ax1.vb)
            self.ax2.vb.setXLink(self.ax1.vb)
        else:
            self.graphLayout.removeItem(self.titleLabel)
            self.graphLayout.addItem(self.titleLabel, row=0, col=1)
            # self.mainLayout.setAlignment(self.bottomLayout, Qt.AlignCenter)  
            self.ax2.hide()
            oldLink = self.ax2.vb.linkedView(self.ax1.vb.YAxis)
            try:
                oldLink.sigYRangeChanged.disconnect()
                oldLink.sigXRangeChanged.disconnect()
            except TypeError:
                pass
    
    def showRightImageItem(self, checked):
        self.setTwoImagesLayout(checked)
        if checked:
            self.df_settings.at['isRightImageVisible', 'value'] = 'Yes'
            self.graphLayout.addItem(
                self.imgGradRight, row=1, col=self.plotsCol+2
            )
            self.rightBottomGroupbox.show()
            if not self.isDataLoading:
                self.updateAllImages()
        else:
            self.clearAx2Items()
            self.rightBottomGroupbox.hide()
            self.df_settings.at['isRightImageVisible', 'value'] = 'No'
            try:
                self.graphLayout.removeItem(self.imgGradRight)
            except Exception:
                return
            self.rightImageItem.clear()
        
        self.df_settings.to_csv(self.settings_csv_path)
            
        QTimer.singleShot(300, self.resizeGui)

        self.setBottomLayoutStretch()    
        
    def showLabelImageItem(self, checked):
        self.setTwoImagesLayout(checked)
        self.rightBottomGroupbox.hide()
        if checked:
            self.df_settings.at['isLabelsVisible', 'value'] = 'Yes'
            self.updateAllImages()
        else:
            self.clearAx2Items()
            self.img2.clear()
            self.df_settings.at['isLabelsVisible', 'value'] = 'No'
            # Move del ROIs to the left image
            for posData in self.data:
                delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
                for roi in delROIs_info['rois']:
                    if roi not in self.ax2.items:
                        continue

                    self.ax1.addItem(roi)
                    # self.ax2.removeItem(roi)
        
        self.df_settings.to_csv(self.settings_csv_path)
        QTimer.singleShot(200, self.resizeGui)

        self.setBottomLayoutStretch()

    def setBottomLayoutStretch(self):
        if self.labelsGrad.showRightImgAction.isChecked():
            # Equally share space between the two control groupboxes
            self.bottomLayout.setStretch(1, 1)
            self.bottomLayout.setStretch(2, 5)
            self.bottomLayout.setStretch(3, 1)
            self.bottomLayout.setStretch(4, 5)
            self.bottomLayout.setStretch(5, 1)
        elif self.labelsGrad.showLabelsImgAction.isChecked():
            # Left control takes only left space
            self.bottomLayout.setStretch(1, 1)
            self.bottomLayout.setStretch(2, 5)
            self.bottomLayout.setStretch(3, 5)
            self.bottomLayout.setStretch(4, 1)
            self.bottomLayout.setStretch(5, 1)
        else:
            # Left control takes all the space
            self.bottomLayout.setStretch(1, 3)
            self.bottomLayout.setStretch(2, 10)
            self.bottomLayout.setStretch(3, 1)
            self.bottomLayout.setStretch(4, 1)
            self.bottomLayout.setStretch(5, 1)

    def setCheckedInvertBW(self, checked):
        self.invertBwAction.setChecked(checked)

    def ticksCmapMoved(self, gradient):
        pass
        # posData = self.data[self.pos_i]
        # self.setLut(posData, shuffle=False)
        # self.updateLookuptable()

    def updateLabelsCmap(self, gradient):
        self.setLut()
        self.updateLookuptable()
        self.initLabelsImageItems()

        self.df_settings = self.labelsGrad.saveState(self.df_settings)
        self.df_settings.to_csv(self.settings_csv_path)

        self.updateAllImages()

    def updateBkgrColor(self, button):
        color = button.color().getRgb()[:3]
        self.lut[0] = color
        self.updateLookuptable()

    def updateTextLabelsColor(self, button):
        self.ax2_textColor = button.color().getRgb()[:3]
        posData = self.data[self.pos_i]
        if posData.rp is None:
            return

        for obj in posData.rp:
            self.getObjOptsSegmLabels(obj)

    def saveTextLabelsColor(self, button):
        color = button.color().getRgb()[:3]
        self.df_settings.at['labels_text_color', 'value'] = color
        self.df_settings.to_csv(self.settings_csv_path)

    def saveBkgrColor(self, button):
        color = button.color().getRgb()[:3]
        self.df_settings.at['labels_bkgrColor', 'value'] = color
        self.df_settings.to_csv(self.settings_csv_path)
        self.updateAllImages()

    def changeOverlayColor(self, button):
        rgb = button.color().getRgb()[:3]
        lutItem = self.overlayLayersItems[button.channel][1]
        self.initColormapOverlayLayerItem(rgb, lutItem)
        lutItem.overlayColorButton.setColor(rgb)
    
    def saveOverlayColor(self, button):
        rgb = button.color().getRgb()[:3]
        rgb_text = '_'.join([str(val) for val in rgb])
        self.df_settings.at[f'{button.channel}_rgb', 'value'] = rgb_text
        self.df_settings.to_csv(self.settings_csv_path)

    def getImageDataFromFilename(self, filename):
        posData = self.data[self.pos_i]
        if filename == posData.filename:
            return posData.img_data[posData.frame_i]
        else:
            return posData.ol_data_dict.get(filename)

    def get_2Dimg_from_3D(self, imgData, isLayer0=True, frame_i=None):
        posData = self.data[self.pos_i]
        if frame_i is None:
            frame_i = posData.frame_i
        idx = (posData.filename, frame_i)
        zProjHow_L0 = self.zProjComboBox.currentText()
        if isLayer0:
            try:
                z = posData.segmInfo_df.at[idx, 'z_slice_used_gui']
            except ValueError as e:
                z = posData.segmInfo_df.loc[idx, 'z_slice_used_gui'].iloc[0] 
            zProjHow = zProjHow_L0
        else:
            z = self.zSliceOverlay_SB.sliderPosition()
            zProjHow_L1 = self.zProjOverlay_CB.currentText()
            if zProjHow_L1 == 'same as above': 
                zProjHow = zProjHow_L0
            else:
                zProjHow = zProjHow_L1
        
        if zProjHow == 'single z-slice':
            img = imgData[z].copy()
        elif zProjHow == 'max z-projection':
            img = imgData.max(axis=0).copy()
        elif zProjHow == 'mean z-projection':
            img = imgData.mean(axis=0).copy()
        elif zProjHow == 'median z-proj.':
            img = np.median(imgData, axis=0).copy()
        return img

    def updateZsliceScrollbar(self, frame_i):
        posData = self.data[self.pos_i]
        idx = (posData.filename, frame_i)
        try:
            z = posData.segmInfo_df.at[idx, 'z_slice_used_gui']
        except ValueError as e:
            z = posData.segmInfo_df.loc[idx, 'z_slice_used_gui'].iloc[0] 
        try:
            zProjHow = posData.segmInfo_df.at[idx, 'which_z_proj_gui']
        except ValueError as e:
            zProjHow = posData.segmInfo_df.loc[idx, 'which_z_proj_gui'].iloc[0] 
        if zProjHow != 'single z-slice':
            return
        reconnect = False
        try:
            self.zSliceScrollBar.actionTriggered.disconnect()
            self.zSliceScrollBar.sliderReleased.disconnect()
            reconnect = True
        except TypeError:
            pass
        self.zSliceScrollBar.setSliderPosition(z)
        if reconnect:
            self.zSliceScrollBar.actionTriggered.connect(
                self.zSliceScrollBarActionTriggered
            )
            self.zSliceScrollBar.sliderReleased.connect(
                self.zSliceScrollBarReleased
            )
        self.zSliceSpinbox.setValueNoEmit(z+1)
    
    def getRawImage(self, frame_i=None, filename=None):
        posData = self.data[self.pos_i]
        if frame_i is None:
            frame_i = posData.frame_i
        if filename is None:
            rawImgData = posData.img_data[frame_i]
            isLayer0 = True
        else: 
            rawImgData = posData.ol_data[filename][frame_i]
            isLayer0 = False
        if posData.SizeZ > 1:
            rawImg = self.get_2Dimg_from_3D(rawImgData, isLayer0=isLayer0)
        else:
            rawImg = rawImgData
        return rawImg
        
    def getImage(self, frame_i=None, normalizeIntens=True):
        posData = self.data[self.pos_i]
        if frame_i is None:
            frame_i = posData.frame_i
        if posData.SizeZ > 1:
            img = posData.img_data[frame_i]
            self.updateZsliceScrollbar(frame_i)
            cells_img = self.get_2Dimg_from_3D(img)
        else:
            cells_img = posData.img_data[frame_i].copy()
        if normalizeIntens:
            cells_img = self.normalizeIntensities(cells_img)
        if self.imgCmapName != 'grey':
            # Do not invert bw for non grey cmaps
            return cells_img
        return cells_img

    def setImageImg2(self, updateLookuptable=True, set_image=True):
        posData = self.data[self.pos_i]
        mode = str(self.modeComboBox.currentText())
        if mode == 'Segmentation and Tracking' or self.isSnapshot:
            self.addExistingDelROIs()
            allDelIDs, DelROIlab = self.getDelROIlab()
        else:
            DelROIlab = self.get_2Dlab(posData.lab, force_z=False)
            allDelIDs = set()
        if self.labelsGrad.showLabelsImgAction.isChecked() and set_image:
            self.img2.setImage(DelROIlab, z=self.z_lab(), autoLevels=False)
        self.currentLab2D = DelROIlab
        if updateLookuptable:
            self.updateLookuptable(delIDs=allDelIDs)

    def applyDelROIimg1(self, roi, init=False, ax=0):
        if ax == 0:
            how = self.drawIDsContComboBox.currentText()
        else:
            how = self.getAnnotateHowRightImage()
        
        if ax == 1 and not self.labelsGrad.showRightImgAction.isChecked():
            return
        
        if init and how.find('contours') == -1:
            self.setOverlaySegmMasks(force=True)
            return

        posData = self.data[self.pos_i]
        delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
        idx = delROIs_info['rois'].index(roi)
        delIDs = delROIs_info['delIDsROI'][idx]
        delMask = delROIs_info['delMasks'][idx]
        if how.find('nothing') != -1:
            return
        elif how.find('contours') != -1:
            self.updateContoursImage(ax=ax)
        
        if not delIDs:
            return
        
        if how.find('overlay segm. masks') != -1:
            lab = self.currentLab2D.copy()
            lab[delMask] = 0
            if ax == 0:
                self.labelsLayerImg1.setImage(lab, autoLevels=False)
            else:
                self.labelsLayerRightImg.setImage(lab, autoLevels=False)

        self.setAllTextAnnotations(labelsToSkip={ID:True for ID in delIDs})
    
    def applyDelROIs(self):
        self.logger.info('Applying deletion ROIs (if present)...')
        
        for posData in self.data:
            self.current_frame_i = posData.frame_i
            for frame_i in range(posData.SizeT):
                lab = posData.allData_li[frame_i]['labels']
                if lab is None:
                    break
                delROIs_info = posData.allData_li[frame_i]['delROIs_info']
                delIDs_rois = delROIs_info['delIDsROI']
                if not delIDs_rois:
                    continue
                for delIDs in delIDs_rois:
                    for delID in delIDs:
                        lab[lab==delID] = 0
                posData.allData_li[frame_i]['labels'] = lab
                # Get the rest of the metadata and store data based on the new lab
                posData.frame_i = frame_i
                self.get_data()
                self.store_data(autosave=False)
            
            # Back to current frame
            posData.frame_i = self.current_frame_i
            self.get_data()
                
    def initTempLayerBrush(self, ID, ax=0):
        if ax == 0:
            how = self.drawIDsContComboBox.currentText()
        else:
            how = self.getAnnotateHowRightImage()
        
        self.hideItemsHoverBrush(ID=ID, force=True)
        Y, X = self.img1.image.shape[:2]
        tempImage = np.zeros((Y, X), dtype=np.uint32)
        if how.find('contours') != -1:
            tempImage[self.currentLab2D==ID] = ID
            self.brushImage = tempImage.copy()
            self.brushContourImage = np.zeros((Y, X, 4), dtype=np.uint8)
            color = self.imgGrad.contoursColorButton.color()
            self.brushContoursRgba = color.getRgb()
            opacity = 1.0
        else:
            opacity = self.imgGrad.labelsAlphaSlider.value()
            color = self.lut[ID]
            lut = np.zeros((2, 4), dtype=np.uint8)
            lut[1,-1] = 255
            lut[1,:-1] = color
            self.tempLayerImg1.setLookupTable(lut)
        self.tempLayerImg1.setOpacity(opacity)
        self.tempLayerImg1.setImage(tempImage)
    
    def _setTempImageBrushContour(self):
        pass
    
    # @exec_time
    def setTempImg1Brush(self, init: bool, mask, ID, toLocalSlice=None, ax=0):
        if init:
            self.initTempLayerBrush(ID, ax=ax)
        
        if self.annotContourCheckbox.isChecked():
            brushImage = self.brushImage
        else:
            brushImage = self.tempLayerImg1.image
            
        if toLocalSlice is None:
            brushImage[mask] = ID
        else:
            brushImage[toLocalSlice][mask] = ID
        
        if self.annotContourCheckbox.isChecked():
            obj = skimage.measure.regionprops(brushImage)[0]
            objContour = [self.getObjContours(obj)]
            self.brushContourImage[:] = 0
            img = self.brushContourImage
            color = self.brushContoursRgba
            cv2.drawContours(img, objContour, -1, color, 1)
            self.tempLayerImg1.setImage(img)
        else:
            self.tempLayerImg1.setImage(brushImage)
    
    def getLabelsLayerImage(self, ax=0):
        if ax == 0:
            return self.labelsLayerImg1.image
        else:
            return self.labelsLayerRightImg.image
    
    def clearObjFromMask(self, image, mask, toLocalSlice=None):
        if mask is None:
            return image

        if toLocalSlice is None:
            image[mask] = 0
        else:
            image[toLocalSlice][mask] = 0
        
        return image
    
    # @exec_time
    def setTempImg1Eraser(self, mask, init=False, toLocalSlice=None, ax=0):
        if init:
            self.erasedLab = np.zeros_like(self.currentLab2D)  

        if ax == 0:
            how = self.drawIDsContComboBox.currentText()
        else:
            how = self.getAnnotateHowRightImage()
        
        if ax == 1 and not self.labelsGrad.showRightImgAction.isChecked():
            return
        
        if how.find('contours') != -1:
            self.clearObjFromMask(
                self.contoursImage, mask, toLocalSlice=toLocalSlice
            )
            erasedRp = skimage.measure.regionprops(self.erasedLab)
            for obj in erasedRp:
                self.addObjContourToContoursImage(obj=obj, ax=ax)
        elif how.find('overlay segm. masks') != -1:
            labelsImage = self.getLabelsLayerImage(ax=ax)
            self.clearObjFromMask(labelsImage, mask, toLocalSlice=toLocalSlice)           
            if ax == 0:
                self.labelsLayerImg1.setImage(
                    self.labelsLayerImg1.image, autoLevels=False)
            else:
                self.labelsLayerRightImg.setImage(
                    self.labelsLayerRightImg.image, autoLevels=False)

    def setTempImg1ExpandLabel(self, prevCoords, expandedObjCoords, ax=0):
        if ax == 0:
            how = self.drawIDsContComboBox.currentText()
        else:
            how = self.getAnnotateHowRightImage()
        
        if how.find('overlay segm. masks') != -1:
            # Remove previous overlaid mask
            labelsImage = self.getLabelsLayerImage(ax=ax)
            labelsImage[prevCoords] = 0
            
            # Overlay new moved mask
            labelsImage[prevCoords] = self.expandingID

            if ax == 0:
                self.labelsLayerImg1.setImage(
                    self.labelsLayerImg1.image, autoLevels=False)
            else:
                self.labelsLayerRightImg.setImage(
                    self.labelsLayerRightImg.image, autoLevels=False)
        else:
            currentLab2Drp = skimage.measure.regionprops(self.currentLab2D)
            for obj in currentLab2Drp:
                if obj.label == self.expandingID:
                    self.clearObjContour(obj=obj, ax=ax)
                    self.addObjContourToContoursImage(obj=obj, ax=ax)
                    break

    def setTempImg1MoveLabel(self, ax=0):
        if ax == 0:
            how = self.drawIDsContComboBox.currentText()
        else:
            how = self.getAnnotateHowRightImage()
        
        if how.find('contours') != -1:
            currentLab2Drp = skimage.measure.regionprops(self.currentLab2D)
            for obj in currentLab2Drp:
                if obj.label == self.movingID:
                    self.addObjContourToContoursImage(obj=obj, ax=ax)
                    break
        elif how.find('overlay segm. masks') != -1:
            if ax == 0:
                self.labelsLayerImg1.setImage(self.currentLab2D, autoLevels=False)
                self.highLightIDLayerImg1.image[:] = 0
                mask = self.currentLab2D==self.movingID
                self.highLightIDLayerImg1.image[mask] = self.movingID
                highlightedImage = self.highLightIDLayerImg1.image
                self.highLightIDLayerImg1.setImage(highlightedImage)
            else:
                self.labelsLayerRightImg.setImage(
                    self.currentLab2D, autoLevels=False
                )
                self.highLightIDLayerRightImage.image[:] = 0
                mask = self.currentLab2D==self.movingID
                self.highLightIDLayerRightImage.image[mask] = self.movingID
                highlightedImage = self.highLightIDLayerRightImage.image
                self.highLightIDLayerRightImage.setImage(highlightedImage)

    def addMissingIDs_cca_df(self, posData):
        base_cca_df = self.getBaseCca_df()
        posData.cca_df = posData.cca_df.combine_first(base_cca_df)

    def update_cca_df_relabelling(self, posData, oldIDs, newIDs):
        relIDs = posData.cca_df['relative_ID']
        posData.cca_df['relative_ID'] = relIDs.replace(oldIDs, newIDs)
        mapper = dict(zip(oldIDs, newIDs))
        posData.cca_df = posData.cca_df.rename(index=mapper)

    def update_cca_df_deletedIDs(self, posData, deleted_IDs):
        relIDs = posData.cca_df.reindex(deleted_IDs, fill_value=-1)['relative_ID']
        posData.cca_df = posData.cca_df.drop(deleted_IDs, errors='ignore')
        self.update_cca_df_newIDs(posData, relIDs)

    def update_cca_df_newIDs(self, posData, new_IDs):
        for newID in new_IDs:
            self.addIDBaseCca_df(posData, newID)

    def update_cca_df_snapshots(self, editTxt, posData):
        cca_df = posData.cca_df
        cca_df_IDs = cca_df.index
        if editTxt == 'Delete ID':
            deleted_IDs = [ID for ID in cca_df_IDs if ID not in posData.IDs]
            self.update_cca_df_deletedIDs(posData, deleted_IDs)

        elif editTxt == 'Separate IDs':
            new_IDs = [ID for ID in posData.IDs if ID not in cca_df_IDs]
            self.update_cca_df_newIDs(posData, new_IDs)
            deleted_IDs = [ID for ID in cca_df_IDs if ID not in posData.IDs]
            self.update_cca_df_deletedIDs(posData, deleted_IDs)

        elif editTxt == 'Edit ID':
            new_IDs = [ID for ID in posData.IDs if ID not in cca_df_IDs]
            self.update_cca_df_newIDs(posData, new_IDs)
            old_IDs = [ID for ID in cca_df_IDs if ID not in posData.IDs]
            self.update_cca_df_deletedIDs(posData, old_IDs)

        elif editTxt == 'Annotate ID as dead':
            return
        
        elif editTxt == 'Deleted non-selected objects':
            deleted_IDs = [ID for ID in cca_df_IDs if ID not in posData.IDs]
            self.update_cca_df_deletedIDs(posData, deleted_IDs)

        elif editTxt == 'Delete ID with eraser':
            deleted_IDs = [ID for ID in cca_df_IDs if ID not in posData.IDs]
            self.update_cca_df_deletedIDs(posData, deleted_IDs)

        elif editTxt == 'Add new ID with brush tool':
            new_IDs = [ID for ID in posData.IDs if ID not in cca_df_IDs]
            self.update_cca_df_newIDs(posData, new_IDs)

        elif editTxt == 'Merge IDs':
            deleted_IDs = [ID for ID in cca_df_IDs if ID not in posData.IDs]
            self.update_cca_df_deletedIDs(posData, deleted_IDs)

        elif editTxt == 'Add new ID with curvature tool':
            new_IDs = [ID for ID in posData.IDs if ID not in cca_df_IDs]
            self.update_cca_df_newIDs(posData, new_IDs)

        elif editTxt == 'Delete IDs using ROI':
            deleted_IDs = [ID for ID in cca_df_IDs if ID not in posData.IDs]
            self.update_cca_df_deletedIDs(posData, deleted_IDs)

        elif editTxt == 'Repeat segmentation':
            posData.cca_df = self.getBaseCca_df()

        elif editTxt == 'Random Walker segmentation':
            posData.cca_df = self.getBaseCca_df()
    
    def fixCcaDfAfterEdit(self, editTxt):
        posData = self.data[self.pos_i]
        if posData.cca_df is not None:
            # For snapshot mode we fix or reinit cca_df depending on the edit
            self.update_cca_df_snapshots(editTxt, posData)
            self.store_data()

    def warnEditingWithCca_df(
            self, editTxt, return_answer=False, get_answer=False, 
            get_cancelled=False
        ):
        # Function used to warn that the user is editing in "Segmentation and
        # Tracking" mode a frame that contains cca annotations.
        # Ask whether to remove annotations from all future frames 
        if self.isSnapshot:
            return True

        posData = self.data[self.pos_i]
        acdc_df = posData.allData_li[posData.frame_i]['acdc_df']
        if acdc_df is None:
            self.updateAllImages()
            return True
        else:
            if 'cell_cycle_stage' not in acdc_df.columns:
                self.updateAllImages()
                return True
        action = self.warnEditingWithAnnotActions.get(editTxt, None)
        if action is not None:
            if not action.isChecked():
                self.updateAllImages()
                return True

        msg = widgets.myMessageBox()
        txt = html_utils.paragraph(
            'You modified a frame that <b>has cell cycle annotations</b>.<br><br>'
            f'The change <b>"{editTxt}"</b> most likely makes the '
            '<b>annotations wrong</b>.<br><br>'
            'If you really want to apply this change we reccommend to remove'
            'ALL cell cycle annotations<br>'
            'from current frame to the end.<br><br>'
            'What do you want to do?'
        )
        if action is not None:
            checkBox = QCheckBox('Remember my choice and do not ask again')
        else:
            checkBox = None
        
        dropDelIDsNoteText = (
            '' if editTxt.find('Delete') == -1 else ' (drop removed IDs)'
        )
        _, removeAnnotButton, _ = msg.warning(
            self, 'Edited segmentation with annotations!', txt,
            buttonsTexts=(
                'Cancel',
                'Remove annotations from future frames (RECOMMENDED)',
                f'Do not remove annotations{dropDelIDsNoteText}'
            ), widgets=checkBox
            )
        if msg.cancel:
            if get_cancelled:
                return 'cancelled'
            removeAnnotations = False
            return removeAnnotations
        
        if action is not None:
            action.setChecked(not checkBox.isChecked())
            action.removeAnnot = msg.clickedButton == removeAnnotButton
        
        if return_answer:
            return msg.clickedButton == removeAnnotButton
        
        if msg.clickedButton == removeAnnotButton:
            self.store_data()
            posData.frame_i -= 1
            self.get_data()
            self.remove_future_cca_df(posData.frame_i)
            self.next_frame(warn=False)
        else:
            if dropDelIDsNoteText and posData.cca_df is not None:
                delIDs = [
                    ID for ID in posData.cca_df.index if ID not in posData.IDs
                ]
                self.update_cca_df_deletedIDs(posData, delIDs)
            self.addMissingIDs_cca_df(posData)
            self.updateAllImages()
            self.store_data()
        if action is not None:
            if action.removeAnnot:
                self.store_data()
                posData.frame_i -= 1
                self.get_data()
                self.remove_future_cca_df(posData.frame_i)
                self.next_frame()
        
        if get_answer:
            return msg.clickedButton == removeAnnotButton
        else:
            return True
    
    def warnRepeatTrackingVideoWithAnnotations(self, last_tracked_i, start_n):
        msg = widgets.myMessageBox()
        txt = html_utils.paragraph(
            'You are repeating tracking on frames that <b>have cell cycle '
            'annotations</b>.<br><br>'
            'This will very likely make the <b>annotations wrong</b>.<br><br>'
            'If you really want to repeat tracking on the frames before '
            f'{last_tracked_i+1} the <b>annotations from frame '
            f'{start_n} to frame {last_tracked_i+1} '
            'will be removed</b>.<br><br>'
            'Do you want to continue?'
        )
        noButton, yesButton = msg.warning(
            self, 'Repating tracking with annotations!', txt,
            buttonsTexts=(
                '  No, stop tracking and keep annotations.',
                '  Yes, repeat tracking and DELETE annotations.' 
            )
        )
        if msg.cancel:
            return False

        if msg.clickedButton == noButton:
            return False
        else:
            return True

    def addExistingDelROIs(self):
        posData = self.data[self.pos_i]
        delROIs_info = posData.allData_li[posData.frame_i]['delROIs_info']
        for roi in delROIs_info['rois']:
            if roi in self.ax2.items or roi in self.ax1.items:
                continue
            if isinstance(roi, pg.PolyLineROI):
                # PolyLine ROIs are only on ax1
                self.ax1.addItem(roi)
            elif not self.labelsGrad.showLabelsImgAction.isChecked():
                # Rect ROI is on ax1 because ax2 is hidden
                self.ax1.addItem(roi)
            else:
                # Rect ROI is on ax2 because ax2 is visible
                self.ax2.addItem(roi)    

    def updateFramePosLabel(self):
        if self.isSnapshot:
            posData = self.data[self.pos_i]
            self.navSpinBox.setValueNoEmit(self.pos_i+1)
        else:
            posData = self.data[0]
            self.navSpinBox.setValueNoEmit(posData.frame_i+1)

    def updateFilters(self):
        pass
    
    def highlightHoverID(self, x, y, hoverID=None):
        if hoverID is None:
            try:
                hoverID = self.currentLab2D[int(y), int(x)]
            except IndexError:
                return

        self.highlightSearchedID(hoverID, isHover=True)
    
    def highlightHoverIDsKeptObj(self, x, y, hoverID=None):
        if hoverID is None:
            try:
                hoverID = self.currentLab2D[int(y), int(x)]
            except IndexError:
                return

        self.highlightSearchedID(hoverID, isHover=True)
        for ID in self.keptObjectsIDs:
            self.highlightLabelID(ID)

    def highlightSearchedID(self, ID, force=False, isHover=False):
        if ID == 0:
            return

        if ID == self.highlightedID and not force:
            return

        if self.highlightedID > 0:
            self.clearHighlightedText()
        
        self.searchedIDitemRight.setData([], [])
        self.searchedIDitemLeft.setData([], [])

        posData = self.data[self.pos_i]

        how_ax1 = self.drawIDsContComboBox.currentText()
        how_ax2 = self.getAnnotateHowRightImage()
        isOverlaySegm_ax1 = how_ax1.find('segm. masks') != -1 
        isOverlaySegm_ax2 = how_ax2.find('segm. masks') != -1

        self.highlightedID = ID

        if ID not in posData.IDs:
            return

        self.textAnnot[0].grayOutAnnotations()
        self.textAnnot[1].grayOutAnnotations()

        objIdx = posData.IDs.index(ID)
        obj = posData.rp[objIdx]

        if not isHover:
            self.goToZsliceSearchedID(obj)

        if isOverlaySegm_ax1 or isOverlaySegm_ax2:
            alpha = self.imgGrad.labelsAlphaSlider.value()
            highlightedLab = np.zeros_like(self.currentLab2D)
            lut = np.zeros((2, 4), dtype=np.uint8)
            for _obj in posData.rp:
                if not self.isObjVisible(_obj.bbox):
                    continue
                if _obj.label != obj.label:
                    continue
                _slice = self.getObjSlice(_obj.slice)
                _objMask = self.getObjImage(_obj.image, _obj.bbox)
                highlightedLab[_slice][_objMask] = _obj.label
                rgb = self.lut[_obj.label].copy()    
                lut[1, :-1] = rgb
                # Set alpha to 0.7
                lut[1, -1] = 178          
        
        cont = None
        contours = None
        if isOverlaySegm_ax1:
            self.highLightIDLayerImg1.setLookupTable(lut)
            self.highLightIDLayerImg1.setImage(highlightedLab)          
            self.labelsLayerImg1.setOpacity(alpha/3)
        else:
            contours = self.getObjContours(obj, all_external=True)
            for cont in contours:
                self.searchedIDitemLeft.addPoints(cont[:,0]+0.5, cont[:,1]+0.5)
        
        if isOverlaySegm_ax2:
            self.highLightIDLayerRightImage.setLookupTable(lut)
            self.highLightIDLayerRightImage.setImage(highlightedLab)
            self.labelsLayerRightImg.setOpacity(alpha/3)
        else:
            if contours is None:
                contours = self.getObjContours(obj, all_external=True)
            for cont in contours:
                self.searchedIDitemRight.addPoints(cont[:,0]+0.5, cont[:,1]+0.5)       

        # Gray out all IDs excpet searched one
        lut = self.lut.copy() # [:max(posData.IDs)+1]
        lut[:ID] = lut[:ID]*0.2
        lut[ID+1:] = lut[ID+1:]*0.2
        self.img2.setLookupTable(lut)

        # Highlight text
        self.highlightLabelID(ID, ax=0)
        self.highlightLabelID(ID, ax=1)
    
    def _drawGhostContour(self, x, y):
        if self.ghostObject is None:
            return
        
        ID = self.ghostObject.label
        yc, xc = self.ghostObject.local_centroid
        Dx = x-xc
        Dy = y-yc
        xx = self.ghostObject.xx_contour + Dx
        yy = self.ghostObject.yy_contour + Dy
        self.ghostContourItemLeft.setData(
            xx, yy, fontSize=self.fontSize, ID=ID, y_cursor=y, x_cursor=x
        )
        self.ghostContourItemRight.setData(
            xx, yy, fontSize=self.fontSize, ID=ID, y_cursor=y, x_cursor=x
        )
    
    def _drawGhostMask(self, x, y):
        if self.ghostObject is None:
            return
        
        self.clearGhostMask()
        ID = self.ghostObject.label
        h, w = self.ghostObject.image.shape[-2:]
        yc, xc = self.ghostObject.local_centroid
        Dx = int(x-xc)
        Dy = int(y-yc)
        bbox = ((Dy, Dy+h), (Dx, Dx+w))

        Y, X = self.currentLab2D.shape
        slices = myutils.get_slices_local_into_global_arr(bbox, (Y, X))
        slice_global_to_local, slice_crop_local = slices

        obj_image = self.ghostObject.image[slice_crop_local]

        self.ghostMaskItemLeft.image[slice_global_to_local][obj_image] = ID
        self.ghostMaskItemLeft.updateGhostImage(
            fontSize=self.fontSize, ID=ID, y_cursor=y, x_cursor=x
        )

        self.ghostMaskItemRight.image[slice_global_to_local][obj_image] = ID
        self.ghostMaskItemRight.updateGhostImage(
            fontSize=self.fontSize, ID=ID, y_cursor=y, x_cursor=x
        )


    def drawManualTrackingGhost(self, x, y):
        if not self.manualTrackingToolbar.showGhostCheckbox.isChecked():
            return
        
        if self.manualTrackingToolbar.ghostContourRadiobutton.isChecked():
            self._drawGhostContour(x, y)
        else:
            self._drawGhostMask(x, y)

    def restoreDefaultSettings(self):
        df = self.df_settings
        df.at['contLineWeight', 'value'] = 1
        df.at['mothBudLineSize', 'value'] = 1
        df.at['mothBudLineColor', 'value'] = (255, 165, 0, 255)
        df.at['contLineColor', 'value'] = (205, 0, 0, 220)

        self._updateContColour((205, 0, 0, 220))
        self._updateMothBudLineColour((255, 165, 0, 255))
        self._updateMothBudLineSize(1)
        self._updateContLineThickness()

        df.at['overlaySegmMasksAlpha', 'value'] = 0.3
        df.at['img_cmap', 'value'] = 'grey'
        self.imgCmap = self.imgGrad.cmaps['grey']
        self.imgCmapName = 'grey'
        self.labelsGrad.item.loadPreset('viridis')
        df.at['labels_bkgrColor', 'value'] = (25, 25, 25)
        
        if df.at['is_bw_inverted', 'value'] == 'Yes':
            self.invertBw(update=False)
        
        df = df[~df.index.str.contains('lab_cmap')]
        df.to_csv(self.settings_csv_path)
        self.imgGrad.restoreState(df)
        for items in self.overlayLayersItems.values():
            lutItem = items[1]
            lutItem.restoreState(df)

        self.labelsGrad.saveState(df)
        self.labelsGrad.restoreState(df, loadCmap=False)

        self.df_settings.to_csv(self.settings_csv_path)
        self.upateAllImages()

    def updateLabelsAlpha(self, value):
        self.df_settings.at['overlaySegmMasksAlpha', 'value'] = value
        self.df_settings.to_csv(self.settings_csv_path)
        if self.keepIDsButton.isChecked():
            value = value/3
        self.labelsLayerImg1.setOpacity(value)
        self.labelsLayerRightImg.setOpacity(value)

    
    def _getImageupdateAllImages(self, image, updateFilters):
        if image is not None:
            return image
        
        if updateFilters:
            img = self.applyFilter(self.user_ch_name, setImg=False)
        else:
            posData = self.data[self.pos_i]
            filteredData = self.filteredData.get(self.user_ch_name)
            if filteredData is None:
                # Filtered data not existing
                img = self.getImage()
            elif posData.SizeZ > 1:
                # 3D filtered data (see self.applyFilter)
                img = self.get_2Dimg_from_3D(filteredData)
            else:
                # 2D filtered data (see self.applyFilter)
                img = filteredData
        return img
    
    def setImageImg1(self, image, updateFilters):
        img = self._getImageupdateAllImages(image, updateFilters)
        if self.equalizeHistPushButton.isChecked():
            img = skimage.exposure.equalize_adapthist(img)
        self.img1.setImage(img)
    
    def getContoursImageItem(self, ax):
        if not self.areContoursRequested(ax):
            return
        
        if ax == 0:
            return self.ax1_contoursImageItem
        else:
            return self.ax2_contoursImageItem
    
    def updateContoursImage(self, ax, delROIsIDs=None):
        imageItem = self.getContoursImageItem(ax)
        if imageItem is None:
            return
        
        if not hasattr(self, 'contoursImage'):
            self.initContoursImage()
        else:
            self.contoursImage[:] = 0

        contours = []
        for obj in skimage.measure.regionprops(self.currentLab2D):    
            obj_contours = self.getObjContours(obj, all_external=True)  
            contours.extend(obj_contours)

        thickness = self.contLineWeight
        color = self.contLineColor
        self.setContoursImage(imageItem, contours, thickness, color)
    
    def setContoursImage(self, imageItem, contours, thickness, color):
        cv2.drawContours(self.contoursImage, contours, -1, color, thickness)
        imageItem.setImage(self.contoursImage)
    
    def getObjFromID(self, ID):
        posData = self.data[self.pos_i]
        try:
            idx = posData.IDs_idxs[ID]
        except KeyError as e:
            # Object already cleared
            return
        
        obj = posData.rp[idx]
        return obj
    
    def setLostObjectContour(self, obj):
        allContours = self.getObjContours(obj, all_external=True)  
        for objContours in allContours:
            xx = objContours[:,0] + 0.5
            yy = objContours[:,1] + 0.5
            self.ax1_lostObjScatterItem.addPoints(xx, yy)
            self.ax2_lostObjScatterItem.addPoints(xx, yy)
    
    def setCcaIssueContour(self, obj):
        objContours = self.getObjContours(obj, all_external=True)  
        for cont in objContours:
            xx = cont[:,0] + 0.5
            yy = cont[:,1] + 0.5
            self.ax1_lostObjScatterItem.addPoints(xx, yy)
        self.textAnnot[0].addObjAnnotation(
            obj, 'lost_object', f'{obj.label}?', False
        )
    
    def highlightNewCellNotEnoughG1cells(self, IDsCellsG1):
        posData = self.data[self.pos_i]
        for obj in posData.rp:
            if obj.label not in IDsCellsG1:
                continue
            objContours = self.getObjContours(obj)
            if objContours is not None:
                xx = objContours[:,0] + 0.5
                yy = objContours[:,1] + 0.5
                self.ccaFailedScatterItem.addPoints(xx, yy)
            self.textAnnot[0].addObjAnnotation(
                obj, 'green', f'{obj.label}?', False
            )

    def addObjContourToContoursImage(
            self, ID=0, obj=None, ax=0, thickness=None, color=None
        ):        
        imageItem = self.getContoursImageItem(ax)
        if imageItem is None:
            return
        
        if obj is None:
            obj = self.getObjFromID(ID)
            if obj is None:
                return

        # if not self.isObjVisible(obj.bbox):
        #     self.clearObjContour(obj=obj, ax=ax)
        #     return

        contours = self.getObjContours(obj, all_external=True)
        if thickness is None:
            thickness = self.contLineWeight
        if color is None:
            color = self.contLineColor
        
        self.setContoursImage(imageItem, contours, thickness, color)
    
    def clearObjContour(self, ID=0, obj=None, ax=0, debug=False):
        imageItem = self.getContoursImageItem(ax)
        if imageItem is None:
            return

        if ID > 0:
            self.contoursImage[self.currentLab2D==ID] = [0,0,0,0]
        else:
            obj_slice = self.getObjSlice(obj.slice)
            obj_image = self.getObjImage(obj.image, obj.bbox)
            self.contoursImage[obj_slice][obj_image] = [0,0,0,0]
        
        imageItem.setImage(self.contoursImage)        
    
    def clearAnnotItems(self):
        self.textAnnot[0].clear()
        self.textAnnot[1].clear()

    # @exec_time
    def setAllTextAnnotations(self, labelsToSkip=None):
        delROIsIDs = self.setTitleText()
        posData = self.data[self.pos_i]
        self.textAnnot[0].setAnnotations(
            posData=posData, labelsToSkip=labelsToSkip, 
            isVisibleCheckFunc=self.isObjVisible,
            highlightedID=self.highlightedID, 
            delROIsIDs=delROIsIDs,
            annotateLost=self.annotLostObjsToggle.isChecked()
        )
        self.textAnnot[1].setAnnotations(
            posData=posData, labelsToSkip=labelsToSkip, 
            isVisibleCheckFunc=self.isObjVisible,
            highlightedID=self.highlightedID, 
            delROIsIDs=delROIsIDs,
            annotateLost=self.annotLostObjsToggle.isChecked()
        )
        self.textAnnot[0].update()
        self.textAnnot[1].update()
        return delROIsIDs
    
    def setAllContoursImages(self, delROIsIDs=None):
        self.updateContoursImage(ax=0, delROIsIDs=delROIsIDs)
        self.updateContoursImage(ax=1, delROIsIDs=delROIsIDs)

    # @exec_time
    @exception_handler
    def updateAllImages(
            self, image=None, updateFilters=False, computePointsLayers=True
        ):
        self.clearAllItems()

        posData = self.data[self.pos_i]

        if self.last_pos_i != self.pos_i or posData.frame_i != self.last_frame_i:
            updateFilters = True
        
        self.last_pos_i = self.pos_i
        self.last_frame_i = posData.frame_i

        self.setImageImg1(image, updateFilters)       
        self.setImageImg2()
        
        if self.overlayButton.isChecked():
            self.setOverlayImages(updateFilters=updateFilters)

        self.setOverlayLabelsItems()
        self.setOverlaySegmMasks()
              
        if self.slideshowWin is not None:
            self.slideshowWin.frame_i = posData.frame_i
            self.slideshowWin.update_img()

        # self.update_rp()

        # Annotate ID and draw contours
        delROIsIDs = self.setAllTextAnnotations()    
        self.setAllContoursImages(delROIsIDs=delROIsIDs)

        self.drawAllMothBudLines()
        self.highlightLostNew()
        
        self.highlightSearchedID(self.highlightedID, force=True)        

        if self.ccaTableWin is not None:
            self.ccaTableWin.updateTable(posData.cca_df)

        self.doCustomAnnotation(0)

        self.annotate_rip_and_bin_IDs()
        self.updateTempLayerKeepIDs()
        self.drawPointsLayers(computePointsLayers=computePointsLayers)
    
    def setOverlayLabelsItems(self):
        if not self.overlayLabelsButton.isChecked():
            return 

        for segmEndname, drawMode in self.drawModeOverlayLabelsChannels.items():  
            ol_lab = self.getOverlayLabelsData(segmEndname)
            items = self.overlayLabelsItems[segmEndname]
            imageItem, contoursItem, gradItem = items
            contoursItem.clear()
            if drawMode == 'Draw contours':
                for obj in skimage.measure.regionprops(ol_lab):
                    contours = self.getObjContours(
                        obj, all_external=True
                    )
                    for cont in contours:
                        contoursItem.addPoints(cont[:,0]+0.5, cont[:,1]+0.5)
            elif drawMode == 'Overlay labels':
                imageItem.setImage(ol_lab, autoLevels=False)
    
    def getOverlayLabelsData(self, segmEndname):
        posData = self.data[self.pos_i]
        
        if posData.ol_labels_data is None:
            self.loadOverlayLabelsData(segmEndname)            
        elif segmEndname not in posData.ol_labels_data:
            self.loadOverlayLabelsData(segmEndname)
        
        if self.isSegm3D:
            zProjHow = self.zProjComboBox.currentText()
            isZslice = zProjHow == 'single z-slice'
            if isZslice:
                z = self.zSliceScrollBar.sliderPosition()
                return posData.ol_labels_data[segmEndname][posData.frame_i][z]
            else:
                return posData.ol_labels_data[segmEndname][posData.frame_i].max(axis=0)
        else:
            return posData.ol_labels_data[segmEndname][posData.frame_i]
    
    def loadOverlayLabelsData(self, segmEndname):
        posData = self.data[self.pos_i]
        filePath, filename = load.get_path_from_endname(
            segmEndname, posData.images_path
        )
        self.logger.info(f'Loading "{segmEndname}.npz" to overlay...')
        labelsData = np.load(filePath)['arr_0']
        if posData.SizeT == 1:
            labelsData = labelsData[np.newaxis]
        
        if posData.ol_labels_data is None:
            posData.ol_labels_data = {}
        posData.ol_labels_data[segmEndname] = labelsData

    def startBlinkingModeCB(self):
        try:
            self.timer.stop()
            self.stopBlinkTimer.stop()
        except Exception as e:
            pass
        if self.rulerButton.isChecked():
            return
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.blinkModeComboBox)
        self.timer.start(100)
        self.stopBlinkTimer = QTimer(self)
        self.stopBlinkTimer.timeout.connect(self.stopBlinkingCB)
        self.stopBlinkTimer.start(2000)

    def blinkModeComboBox(self):
        if self.flag:
            self.modeComboBox.setStyleSheet('background-color: orange')
        else:
            self.modeComboBox.setStyleSheet('background-color: none')
        self.flag = not self.flag

    def stopBlinkingCB(self):
        self.timer.stop()
        self.modeComboBox.setStyleSheet('background-color: none')

    def highlightNewIDs_ccaFailed(self, IDsWithIssue, rp=None):
        if rp is None:
            posData = self.data[self.pos_i]
            rp = posData.rp
        for obj in rp:
            if obj.label not in IDsWithIssue:
                continue
            self.setCcaIssueContour(obj)

    def highlightLostNew(self):
        if self.modeComboBox.currentText() == 'Viewer':
            return
        
        if not self.annotLostObjsToggle.isChecked():
            return
        
        posData = self.data[self.pos_i]
        delROIsIDs = self.getDelRoisIDs()

        for obj in posData.rp:
            ID = obj.label
            if ID not in posData.new_IDs:
                continue
            if ID in delROIsIDs:
                continue
            
            if not self.isObjVisible(obj.bbox):
                continue
            
            self.addObjContourToContoursImage(
                obj=obj, ax=0, thickness=self.contLineWeight+1
            )
            self.addObjContourToContoursImage(
                obj=obj, ax=1, thickness=self.contLineWeight+1
            )
        
        if not posData.lost_IDs:
            return
        
        if posData.frame_i == 0:
            return 
        
        prev_rp = posData.allData_li[posData.frame_i-1]['regionprops']
        if prev_rp is None:
            return
        for obj in prev_rp:
            if obj.label not in posData.lost_IDs:
                continue
            
            if obj.label in delROIsIDs:
                continue
            
            if not self.isObjVisible(obj.bbox):
                continue
            
            self.setLostObjectContour(obj)
    
    def annotLostObjsToggled(self, checked):
        if not self.dataIsLoaded:
            return
        self.updateAllImages()

    # @exec_time
    def setTitleText(self):
        posData = self.data[self.pos_i]
        if posData.frame_i == 0:
            posData.lost_IDs = []
            posData.new_IDs = []
            posData.old_IDs = []
            # posData.multiContIDs = set()
            self.titleLabel.setText('Looking good!', color=self.titleColor)
            return []
        
        # elif self.modeComboBox.currentText() == 'Viewer':
        #     pass
        
        prev_rp = posData.allData_li[posData.frame_i-1]['regionprops']
        existing = True
        if prev_rp is None:
            prev_lab, existing = self.get_labels(
                frame_i=posData.frame_i-1, return_existing=True
            )
            prev_rp = skimage.measure.regionprops(prev_lab)
            prev_IDs = [obj.label for obj in prev_rp]
        else:
            prev_IDs = posData.allData_li[posData.frame_i-1]['IDs']

        curr_IDs = posData.IDs
        curr_delRoiIDs = self.getStoredDelRoiIDs()
        prev_delRoiIDs = self.getStoredDelRoiIDs(frame_i=posData.frame_i-1)
        lost_IDs = [
            ID for ID in prev_IDs if ID not in curr_IDs 
            and ID not in prev_delRoiIDs
        ]
        new_IDs = [
            ID for ID in curr_IDs if ID not in prev_IDs 
            and ID not in curr_delRoiIDs
        ]
        
        # IDs_with_holes = [
        #     obj.label for obj in posData.rp if obj.area/obj.filled_area < 1
        # ]
        IDs_with_holes = []
        posData.lost_IDs = lost_IDs
        posData.new_IDs = new_IDs
        posData.old_IDs = prev_IDs
        posData.IDs = curr_IDs
        warn_txt = ''
        if existing:
            htmlTxt = ''
        else:
            htmlTxt = f'<font color="white">Never segmented frame. </font>'
        if lost_IDs:
            lost_IDs_format = myutils.get_trimmed_list(lost_IDs)
            warn_txt = f'IDs lost in current frame: {lost_IDs_format}'
            htmlTxt = (
                f'<font color="red">{warn_txt}</font>'
            )
        if new_IDs:
            new_IDs_format = myutils.get_trimmed_list(new_IDs)
            warn_txt = f'New IDs in current frame: {new_IDs_format}'
            htmlTxt = (
                f'{htmlTxt}, <font color="green">{warn_txt}</font>'
            )
        if IDs_with_holes:
            IDs_with_holes_format = myutils.get_trimmed_list(IDs_with_holes)
            warn_txt = f'IDs with holes: {IDs_with_holes_format}'
            htmlTxt = (
                f'{htmlTxt}, <font color="red">{warn_txt}</font>'
            )
        if not htmlTxt:
            warn_txt = 'Looking good'
            color = 'w'
            htmlTxt = (
                f'<font color="{self.titleColor}">{warn_txt}</font>'
            )
        self.titleLabel.setText(htmlTxt)
        return curr_delRoiIDs

    def separateByLabelling(self, lab, rp, maxID=None):
        """
        Label each single object in posData.lab and if the result is more than
        one object then we insert the separated object into posData.lab
        """
        setRp = False
        posData = self.data[self.pos_i]
        if maxID is None:
            maxID = max(posData.IDs, default=1)
        for obj in rp:
            lab_obj = skimage.measure.label(obj.image)
            rp_lab_obj = skimage.measure.regionprops(lab_obj)
            if len(rp_lab_obj)<=1:
                continue
            lab_obj += maxID
            _slice = obj.slice # self.getObjSlice(obj.slice)
            _objMask = obj.image # self.getObjImage(obj.image)
            lab[_slice][_objMask] = lab_obj[_objMask]
            setRp = True
            maxID += 1
        return setRp

    def checkTrackingEnabled(self):
        posData = self.data[self.pos_i]
        posData.last_tracked_i = self.navigateScrollBar.maximum()-1
        if posData.frame_i <= posData.last_tracked_i:
            return True
        else:
            return False

    # @exec_time
    def tracking(
            self, onlyIDs=[], enforce=False, DoManualEdit=True,
            storeUndo=False, prev_lab=None, prev_rp=None,
            return_lab=False, assign_unique_new_IDs=True,
            separateByLabel=True
        ):
        try:
            posData = self.data[self.pos_i]
            mode = str(self.modeComboBox.currentText())
            skipTracking = (
                posData.frame_i == 0 or mode.find('Tracking') == -1
                or self.isSnapshot
            )
            if skipTracking:
                self.setTitleText()
                return

            # Disable tracking for already visited frames
            trackingDisabled = self.checkTrackingEnabled()

            if enforce or self.UserEnforced_Tracking:
                # Tracking enforced by the user
                do_tracking = True
            elif self.UserEnforced_DisabledTracking:
                # Tracking specifically DISABLED by the user
                do_tracking = False
            elif trackingDisabled:
                # User did not choose what to do --> tracking disabled for
                # visited frames and enabled for never visited frames
                do_tracking = False
            else:
                do_tracking = True

            if not do_tracking:
                self.setTitleText()
                return

            """Tracking starts here"""
            staturBarLabelText = self.statusBarLabel.text()
            self.statusBarLabel.setText('Tracking...')

            if storeUndo:
                # Store undo state before modifying stuff
                self.storeUndoRedoStates(False)

            # First separate by labelling
            if separateByLabel:
                setRp = self.separateByLabelling(posData.lab, posData.rp)
                if setRp:
                    self.update_rp()

            if prev_lab is None:
                prev_lab = posData.allData_li[posData.frame_i-1]['labels']
            if prev_rp is None:
                prev_rp = posData.allData_li[posData.frame_i-1]['regionprops']

            if self.trackWithAcdcAction.isChecked():
                tracked_lab = CellACDC_tracker.track_frame(
                    prev_lab, prev_rp, posData.lab, posData.rp,
                    IDs_curr_untracked=posData.IDs,
                    setBrushID_func=self.setBrushID,
                    posData=posData,
                    assign_unique_new_IDs=assign_unique_new_IDs
                )
            elif self.trackWithYeazAction.isChecked():
                tracked_lab = self.tracking_yeaz.correspondence(
                    prev_lab, posData.lab, use_modified_yeaz=True,
                    use_scipy=True
                )
            else:
                tracked_lab = self.realTimeTracker.track_frame(
                    prev_lab, posData.lab, **self.track_frame_params
                )

            if DoManualEdit:
                # Correct tracking with manually changed IDs
                rp = skimage.measure.regionprops(tracked_lab)
                IDs = [obj.label for obj in rp]
                self.manuallyEditTracking(tracked_lab, IDs)

        except ValueError:
            tracked_lab = self.get_2Dlab(posData.lab)

        # Update labels, regionprops and determine new and lost IDs
        posData.lab = tracked_lab
        self.update_rp()
        self.setAllTextAnnotations()
        QTimer.singleShot(50, partial(
            self.statusBarLabel.setText, staturBarLabelText
        ))

    def manuallyEditTracking(self, tracked_lab, allIDs):
        posData = self.data[self.pos_i]
        infoToRemove = []
        # Correct tracking with manually changed IDs
        maxID = max(allIDs, default=1)
        for y, x, new_ID in posData.editID_info:
            old_ID = tracked_lab[y, x]
            if old_ID == 0:
                infoToRemove.append((y, x, new_ID))
                continue
            if new_ID in allIDs:
                tempID = maxID+1
                tracked_lab[tracked_lab == old_ID] = tempID
                tracked_lab[tracked_lab == new_ID] = old_ID
                tracked_lab[tracked_lab == tempID] = new_ID
            else:
                tracked_lab[tracked_lab == old_ID] = new_ID
                if new_ID > maxID:
                    maxID = new_ID
        for info in infoToRemove:
            posData.editID_info.remove(info)
    
    def warnReinitLastSegmFrame(self):
        current_frame_n = self.navigateScrollBar.value()
        msg = widgets.myMessageBox()
        txt = html_utils.paragraph(f"""
            Are you sure you want to <b>re-initialize the last visited and 
            validated</b> frame to number {current_frame_n}?<br><br>
            WARNING: If you save, <b>all annotations after frame number 
            {current_frame_n} will be lost!</b> 
        """)
        msg.warning(
            self, 'WARNING: Potential loss of data', txt,
            buttonsTexts=('Cancel', 'Yes, I am sure')
        )
        return msg.cancel

    def reInitLastSegmFrame(self, checked=True, from_frame_i=None):
        cancel = self.warnReinitLastSegmFrame()
        if cancel:
            self.logger.info('Re-initialization of last validated frame cancelled.')
            return

        posData = self.data[self.pos_i]
        if from_frame_i is None:
            from_frame_i = posData.frame_i
        posData.last_tracked_i = from_frame_i
        self.navigateScrollBar.setMaximum(from_frame_i+1)
        self.navSpinBox.setMaximum(from_frame_i+1)
        # self.navigateScrollBar.setMinimum(1)
        for i in range(from_frame_i, posData.SizeT):
            if posData.allData_li[i]['labels'] is None:
                break
            
            posData.segm_data[i] = posData.allData_li[i]['labels']
            posData.allData_li[i] = {
                'regionprops': [],
                'labels': None,
                'acdc_df': None,
                'delROIs_info': {
                    'rois': [], 'delMasks': [], 'delIDsROI': []
                }
            }

    def removeAllItems(self):
        self.ax1.clear()
        self.ax2.clear()
        try:
            self.chNamesQActionGroup.removeAction(self.userChNameAction)
        except Exception as e:
            pass
        try:
            posData = self.data[self.pos_i]
            for action in self.fluoDataChNameActions:
                self.chNamesQActionGroup.removeAction(action)
        except Exception as e:
            pass
        try:
            self.overlayButton.setChecked(False)
        except Exception as e:
            pass

        if hasattr(self, 'contoursImage'):
            self.initContoursImage()
        
        # for items in self.graphLayout.items.items():
        #     printl(items)
    
    def createUserChannelNameAction(self):
        self.userChNameAction = QAction(self)
        self.userChNameAction.setCheckable(True)
        self.userChNameAction.setText(self.user_ch_name)

    def createChannelNamesActions(self):
        # LUT histogram channel name context menu actions
        self.chNamesQActionGroup = QActionGroup(self) 
        self.chNamesQActionGroup.addAction(self.userChNameAction)
        posData = self.data[self.pos_i]
        for action in self.fluoDataChNameActions:
            self.chNamesQActionGroup.addAction(action)       
            action.setChecked(False)
        
        self.userChNameAction.setChecked(True)

        for action in self.overlayContextMenu.actions():
            action.setChecked(False)

    def restoreDefaultColors(self):
        try:
            color = self.defaultToolBarButtonColor
            self.overlayButton.setStyleSheet(f'background-color: {color}')
        except AttributeError:
            # traceback.print_exc()
            pass

    # Slots
    def newFile(self):
        self.newSegmEndName = ''
        self.isNewFile = True
        msg = widgets.myMessageBox(parent=self, showCentered=False)
        msg.setWindowTitle('File or folder?')
        msg.addText(html_utils.paragraph(f"""
            Do you want to load an <b>image file</b> or <b>Position 
            folder(s)</b>?
        """))
        loadPosButton = QPushButton('Load Position folder', msg)
        loadPosButton.setIcon(QIcon(":folder-open.svg"))
        loadFileButton = QPushButton('Load image file', msg)
        loadFileButton.setIcon(QIcon(":image.svg"))
        helpButton = widgets.helpPushButton('Help...')
        msg.addButton(helpButton)
        helpButton.disconnect()
        helpButton.clicked.connect(self.helpNewFile)
        msg.addCancelButton(connect=True)
        msg.addButton(loadFileButton)
        msg.addButton(loadPosButton)
        loadPosButton.setDefault(True)
        msg.exec_()
        if msg.cancel:
            return
        
        if msg.clickedButton == loadPosButton:
            self._openFolder()
        else:
            self._openFile()
    
    def helpNewFile(self):
        msg = widgets.myMessageBox(showCentered=False)
        href = f'<a href="{user_manual_url}">user manual</a>'
        txt = html_utils.paragraph(f"""
            Cell-ACDC can open both a single image file or files structured 
            into Position folders.<br><br>
            If you are just testing out you can load a single image file, but 
            in general <b>we reccommend structuring your data into Position 
            folders.</b><br><br>
            More info about Position folders in the {href} at the section 
            called "Create required data structure from microscopy file(s)".
        """)
        msg.information(
            self, 'Help on Position folders', txt
        )

    def openFile(self, checked=False, file_path=None):
        self.logger.info(f'Opening FILE "{file_path}"')

        self.isNewFile = False
        self._openFile(file_path=file_path)
    
    def manageVersions(self):
        posData = self.data[self.pos_i]
        selectVersion = apps.SelectAcdcDfVersionToRestore(posData, parent=self)
        selectVersion.exec_()

        if selectVersion.cancel:
            return

        undoId = uuid.uuid4()
        self.storeUndoRedoCca(posData.frame_i, posData.cca_df, undoId)
        
        selectedTime = selectVersion.selectedTimestamp

        self.modeComboBox.setCurrentText('Viewer')
        self.logger.info(f'Loading file from {selectedTime}...')

        key_to_load = selectVersion.selectedKey
        h5_filepath = selectVersion.neverSavedHDFfilepath
        acdc_df = pd.read_hdf(h5_filepath, key=key_to_load)
        posData.acdc_df = acdc_df
        frames = acdc_df.index.get_level_values(0)
        last_visited_frame_i = frames.max()
        current_frame_i = posData.frame_i
        pbar = tqdm(total=last_visited_frame_i+1, ncols=100)
        for frame_i in range(last_visited_frame_i+1):
            posData.frame_i = frame_i
            self.get_data()
            self.storeUndoRedoCca(posData.frame_i, posData.cca_df, undoId)
            if posData.allData_li[frame_i]['labels'] is None:
                pbar.update()
                continue
        
            if frame_i not in frames:
                acdc_df_i = pd.DataFrame(columns=acdc_df.columns)
                acdc_df_i.drop(self.cca_df_colnames, axis=1, errors='ignore')
                acdc_df_i.index.name = 'Cell_ID'
            else:
                acdc_df_i = acdc_df.loc[frame_i].dropna(axis=1, how='all')
            
            posData.allData_li[frame_i]['acdc_df'] = acdc_df_i
            pbar.update()
        pbar.close()
        
        # Back to current frame
        posData.frame_i = current_frame_i
        self.get_data(debug=False)
        self.updateAllImages()

    def warnUserCreationImagesFolder(self, images_path):
        msg = widgets.myMessageBox(wrapText=False)
        txt = html_utils.paragraph(f"""
            To load the data, Cell-ACDC requires the <b>image(s) to be located in a
            folder called <code>Images</code></b>.<br><br>
            The <b>file format</b> of the images must be <b>TIFF</b> (.tif extension).<br><br>
            You can choose to let Cell-ACDC deal with that, or you can stop the 
            process and manually place the image(s) into a folder called 
            <code>Images</code>.<br><br>
            If you choose to proceed, Cell-ACDC will create the following 
            folder:<br><br>
            <code>{images_path}</code>
            <br><br>
            How do you want to proceed?
        """)
        copyButton = widgets.copyPushButton('Copy the image into the new folder')
        moveButton = widgets.movePushButton('Move the image into the new folder')
        _, copyButton, moveButton = msg.warning(
            self, 'Creating Images folder', txt, 
            buttonsTexts=('Cancel', copyButton, moveButton)
        )
        if msg.cancel:
            return False, None

        if msg.clickedButton == copyButton:
            return True, True
        elif msg.clickedButton == moveButton:
            return True, False

    @exception_handler
    def _openFile(self, file_path=None):
        """
        Function used for loading an image file directly.
        """
        if file_path is None:
            self.MostRecentPath = self.getMostRecentPath()
            file_path = QFileDialog.getOpenFileName(
                self, 'Select image file', self.MostRecentPath,
                "Image/Video Files (*.png *.tif *.tiff *.jpg *.jpeg *.mov *.avi *.mp4)"
                ";;All Files (*)")[0]
            if file_path == '':
                return
        dirpath = os.path.dirname(file_path)
        dirname = os.path.basename(dirpath)
        do_copy = True
        if dirname != 'Images':
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            acdc_folder = f'{timestamp}_acdc'
            exp_path = os.path.join(dirpath, acdc_folder, 'Images')
            proceed, do_copy = self.warnUserCreationImagesFolder(exp_path)
            if not proceed:
                self.logger.info('Loading image file aborted.')
                return
            os.makedirs(exp_path, exist_ok=True)
        else:
            exp_path = dirpath

        filename, ext = os.path.splitext(os.path.basename(file_path))
        if ext == '.tif' or ext == '.npz':
            filename_ext = os.path.basename(file_path)
            new_filepath = os.path.join(exp_path, filename_ext)
            if not os.path.exists(new_filepath):
                self.logger.info('Copying file to Image folder...')
                if do_copy:
                    shutil.copy2(file_path, new_filepath)
                else:
                    shutil.move(file_path, new_filepath)
            self._openFolder(exp_path=exp_path, imageFilePath=new_filepath)
        else:
            self.logger.info('Copying file to .tif format...')
            data = load.loadData(file_path, '')
            data.loadImgData()
            img = data.img_data
            if img.ndim == 3 and (img.shape[-1] == 3 or img.shape[-1] == 4):
                self.logger.info('Converting RGB image to grayscale...')
                if img.shape[-1] == 3:
                    data.img_data = skimage.color.rgb2gray(data.img_data)
                else:
                    data.img_data = cv2.cvtColor(
                        data.img_data, cv2.COLOR_RGBA2GRAY
                    )
                data.img_data = skimage.img_as_ubyte(data.img_data)
            tif_path = os.path.join(exp_path, f'{filename}.tif')
            if data.img_data.ndim == 3:
                SizeT = data.img_data.shape[0]
                SizeZ = 1
            elif data.img_data.ndim == 4:
                SizeT = data.img_data.shape[0]
                SizeZ = data.img_data.shape[1]
            else:
                SizeT = 1
                SizeZ = 1
            is_imageJ_dtype = (
                data.img_data.dtype == np.uint8
                or data.img_data.dtype == np.uint32
                or data.img_data.dtype == np.uint32
                or data.img_data.dtype == np.float32
            )
            if not is_imageJ_dtype:
                data.img_data = skimage.img_as_ubyte(data.img_data)

            myutils.imagej_tiffwriter(
                tif_path, data.img_data, {}, SizeT, SizeZ
            )
            self._openFolder(exp_path=exp_path, imageFilePath=tif_path)

    def criticalNoTifFound(self, images_path):
        err_title = 'No .tif files found in folder.'
        err_msg = html_utils.paragraph(
            'The following folder<br><br>'
            f'<code>{images_path}</code><br><br>'
            '<b>does not contain .tif or .h5 files</b>.<br><br>'
            'Only .tif or .h5 files can be loaded with "Open Folder" button.<br><br>'
            'Try with <code>File --> Open image/video file...</code> '
            'and directly select the file you want to load.'
        )
        msg = widgets.myMessageBox()
        msg.addShowInFileManagerButton(images_path)
        msg.critical(self, err_title, err_msg)

    def reInitGui(self):
        self.gui_createLazyLoader()

        try:
            self.navSpinBox.disconnect()
        except Exception as e:
            pass

        self.isZmodifier = False
        self.zKeptDown = False
        self.askRepeatSegment3D = True
        self.askZrangeSegm3D = True
        self.dataIsLoaded = False
        self.retainSizeLutItems = False
        self.showPropsDockButton.setDisabled(True)

        self.reinitWidgetsPos()
        self.removeAllItems()
        self.reinitCustomAnnot()
        self.reinitPointsLayers()
        self.gui_createPlotItems()
        self.setUncheckedAllButtons()
        self.restoreDefaultColors()
        self.curvToolButton.setChecked(False)

        self.navigateToolBar.hide()
        self.ccaToolBar.hide()
        self.editToolBar.hide()
        self.brushEraserToolBar.hide()
        self.modeToolBar.hide()

        self.modeComboBox.setCurrentText('Viewer')
        
        alpha = self.imgGrad.labelsAlphaSlider.value()
        self.labelsLayerImg1.setOpacity(alpha)
        self.labelsLayerRightImg.setOpacity(alpha)
    
    def reinitPointsLayers(self):
        for action in self.pointsLayersToolbar.actions()[1:]:
            self.pointsLayersToolbar.removeAction(action)
        self.pointsLayersToolbar.setVisible(False)
        self.autoPilotZoomToObjToolbar.setVisible(False)
    
    def reinitWidgetsPos(self):
        pass
        # try:
        #     # self.highlightZneighObjCheckbox will be connected in 
        #     # self.showHighlightZneighCheckbox()
        #     self.highlightZneighObjCheckbox.toggled.disconnect()
        # except Exception as e:
        #     pass
        # layout = self.bottomLeftLayout
        # self.highlightZneighObjCheckbox.hide()
        # try:
        #     layout.removeWidget(self.highlightZneighObjCheckbox)
        # except Exception as e:
        #     pass
        # self.highlightZneighObjCheckbox.hide()
        # # layout.addWidget(
        # #     self.drawIDsContComboBox, 0, 1, 1, 2,
        # #     alignment=Qt.AlignCenter
        # # )

    def reinitCustomAnnot(self):
        buttons = list(self.customAnnotDict.keys())
        for button in buttons:
            self.removeCustomAnnotButton(button, save=False, askHow=False)

    def loadingDataAborted(self):
        self.openAction.setEnabled(True)
        self.titleLabel.setText('Loading data aborted.')
    
    def cleanUpOnError(self):
        self.onEscape()
        txt = 'WARNING: Cell-ACDC is in error state. Please, restart.'
        _hl = '===================================='
        self.titleLabel.setText(txt, color='r')
        self.logger.info(f'{_hl}\n{txt}\n{_hl}')

    def openFolder(
            self, checked=False, exp_path=None, imageFilePath=''
        ):
        if exp_path is None:
            self.logger.info('Asking to select a folder path...')
        else:
            self.logger.info(f'Opening FOLDER "{exp_path}"...')

        self.isNewFile = False
        if hasattr(self, 'data') and self.titleLabel.text != 'Saved!':
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph(
                'Do you want to <b>save</b> before loading another dataset?'
            )
            _, no, yes = msg.question(
                self, 'Save?', txt,
                buttonsTexts=('Cancel', 'No', 'Yes')
            )
            if msg.clickedButton == yes:
                func = partial(self._openFolder, exp_path, imageFilePath)
                cancel = self.saveData(finishedCallback=func)
                return
            elif msg.cancel:
                self.store_data()
                return
            else:
                self.store_data(autosave=False)

        self._openFolder(
            exp_path=exp_path, imageFilePath=imageFilePath
        )

    def addToRecentPaths(self, path, logger=None):
        myutils.addToRecentPaths(path, logger=self.logger)
    
    def getMostRecentPath(self):
        return myutils.getMostRecentPath()

    @exception_handler
    def _openFolder(
            self, checked=False, exp_path=None, imageFilePath=''
        ):
        """Main function to load data.

        Parameters
        ----------
        checked : bool
            kwarg needed because openFolder can be called by openFolderAction.
        exp_path : string or None
            Path selected by the user either directly, through openFile,
            or drag and drop image file.
        imageFilePath : string
            Path of the image file that was either drag and dropped or opened
            from File --> Open image/video file (openFileAction).

        Returns
        -------
            None
        """

        if exp_path is None:
            self.MostRecentPath = self.getMostRecentPath()
            exp_path = QFileDialog.getExistingDirectory(
                self,
                'Select experiment folder containing Position_n folders '
                'or specific Position_n folder',
                self.MostRecentPath
            )

        if exp_path == '':
            self.openAction.setEnabled(True)
            return

        self.reInitGui()

        self.openAction.setEnabled(False)

        if self.slideshowWin is not None:
            self.slideshowWin.close()

        if self.ccaTableWin is not None:
            self.ccaTableWin.close()

        self.exp_path = exp_path
        self.logger.info(f'Loading from {self.exp_path}')
        self.addToRecentPaths(exp_path, logger=self.logger)
        self.addPathToOpenRecentMenu(exp_path)

        folder_type = myutils.determine_folder_type(exp_path)
        is_pos_folder, is_images_folder, exp_path = folder_type

        self.titleLabel.setText('Loading data...', color=self.titleColor)

        skip_channels = []
        ch_name_selector = prompts.select_channel_name(
            which_channel='segm', allow_abort=False
        )
        user_ch_name = None
        if not is_pos_folder and not is_images_folder and not imageFilePath:
            images_paths = self._loadFromExperimentFolder(exp_path)
            if not images_paths:
                self.loadingDataAborted()
                return

        elif is_pos_folder and not imageFilePath:
            pos_foldername = os.path.basename(exp_path)
            exp_path = os.path.dirname(exp_path)
            images_paths = [os.path.join(exp_path, pos_foldername, 'Images')]

        elif is_images_folder and not imageFilePath:
            images_paths = [exp_path]
            pos_path = os.path.dirname(exp_path)
            exp_path = os.path.dirname(pos_path)
            
        elif imageFilePath:
            # images_path = exp_path because called by openFile func
            filenames = myutils.listdir(exp_path)
            ch_names, basenameNotFound = (
                ch_name_selector.get_available_channels(filenames, exp_path)
            )
            filename = os.path.basename(imageFilePath)
            self.ch_names = ch_names
            user_ch_name = [
                chName for chName in ch_names if filename.find(chName)!=-1
            ][0]
            images_paths = [exp_path]
            pos_path = os.path.dirname(exp_path)
            exp_path = os.path.dirname(pos_path)

        self.images_paths = images_paths

        # Get info from first position selected
        images_path = self.images_paths[0]
        filenames = myutils.listdir(images_path)
        if ch_name_selector.is_first_call and user_ch_name is None:
            ch_names, _ = ch_name_selector.get_available_channels(
                    filenames, images_path
            )
            self.ch_names = ch_names
            if not ch_names:
                self.openAction.setEnabled(True)
                self.criticalNoTifFound(images_path)
                return
            if len(ch_names) > 1:
                CbLabel='Select channel name to load: '
                ch_name_selector.QtPrompt(
                    self, ch_names, CbLabel=CbLabel
                )
                if ch_name_selector.was_aborted:
                    self.openAction.setEnabled(True)
                    return
                skip_channels.extend([
                    ch for ch in ch_names if ch!=ch_name_selector.channel_name
                ])
            else:
                ch_name_selector.channel_name = ch_names[0]
            ch_name_selector.setUserChannelName()
            user_ch_name = ch_name_selector.user_ch_name
        else:
            # File opened directly with self.openFile
            ch_name_selector.channel_name = user_ch_name

        user_ch_file_paths = []
        not_allowed_ends = ['btrack_tracks.h5']
        for images_path in self.images_paths:
            channel_file_path = load.get_filename_from_channel(
                images_path, user_ch_name, skip_channels=skip_channels,
                not_allowed_ends=not_allowed_ends, logger=self.logger.info
            )
            if not channel_file_path:
                self.criticalImgPathNotFound(images_path)
                return
            user_ch_file_paths.append(channel_file_path)

        ch_name_selector.setUserChannelName()
        self.user_ch_name = user_ch_name

        self.AutoPilotProfile.storeSelectedChannel(self.user_ch_name)

        self.initGlobalAttr()
        self.createOverlayContextMenu()
        self.createUserChannelNameAction()
        self.gui_createOverlayColors()
        self.gui_createOverlayItems()
        lastRow = self.bottomLeftLayout.rowCount()
        self.bottomLeftLayout.setRowStretch(lastRow+1, 1)

        self.num_pos = len(user_ch_file_paths)
        proceed = self.loadSelectedData(user_ch_file_paths, user_ch_name)
        if not proceed:
            self.openAction.setEnabled(True)
            return
    
    def _loadFromExperimentFolder(self, exp_path):
        select_folder = load.select_exp_folder()
        values = select_folder.get_values_segmGUI(exp_path)
        if not values:
            self.criticalInvalidPosFolder(exp_path)
            self.openAction.setEnabled(True)
            return []

        if len(values) > 1:
            select_folder.QtPrompt(self, values, allow_abort=False)
            if select_folder.was_aborted:
                return []
        else:
            select_folder.was_aborted = False
            select_folder.selected_pos = select_folder.pos_foldernames

        images_paths = []
        for pos in select_folder.selected_pos:
            images_paths.append(os.path.join(exp_path, pos, 'Images'))
        return images_paths
    
    def criticalInvalidPosFolder(self, exp_path):
        href = f'<a href="{user_manual_url}">user manual</a>'
        txt = html_utils.paragraph(f"""
            The selected folder:<br><br>
            
            <code>{exp_path}</code><br><br>
            
            is <b>not a valid folder</b>.<br><br>
            
            Select a folder that contains the Position_n folders, 
            or a specific Position.<br><br>
            
            If you are trying to load a single image file go to 
            <code>File --> Open image/video file...</code>.<br><br>
            
            To load a folder containing multiple .tif files the folder must 
            be called either <code>Position_n</code><br>
            (with <code>n</code> being an integer) or <code>Images</code>.<br><br>
            
            You can find <b>more information</b> in the {href} at the 
            section<br>
            "Create required data structure from microscopy file(s)"
        """)
        msg = widgets.myMessageBox(wrapText=False)
        msg.critical(
            self, 'Incompatible folder', txt
        )

    def createOverlayContextMenu(self):
        ch_names = [ch for ch in self.ch_names if ch != self.user_ch_name]
        self.overlayContextMenu = QMenu()
        self.overlayContextMenu.addSeparator()
        self.checkedOverlayChannels = set()
        for chName in ch_names:
            action = QAction(chName, self.overlayContextMenu)
            action.setCheckable(True)
            action.toggled.connect(self.overlayChannelToggled)
            self.overlayContextMenu.addAction(action)
    
    def createOverlayLabelsContextMenu(self, segmEndnames):
        self.overlayLabelsContextMenu = QMenu()
        self.overlayLabelsContextMenu.addSeparator()
        self.drawModeOverlayLabelsChannels = {}
        for segmEndname in segmEndnames:
            action = QAction(segmEndname, self.overlayLabelsContextMenu)
            action.setCheckable(True)
            action.toggled.connect(self.addOverlayLabelsToggled)
            self.overlayLabelsContextMenu.addAction(action)
    
    def createOverlayLabelsItems(self, segmEndnames):
        selectActionGroup = QActionGroup(self)
        for segmEndname in segmEndnames:
            action = QAction(segmEndname)
            action.setCheckable(True)
            action.toggled.connect(self.setOverlayLabelsItemsVisible)
            selectActionGroup.addAction(action)
        self.selectOverlayLabelsActionGroup = selectActionGroup

        self.overlayLabelsItems = {}
        for segmEndname in segmEndnames:
            imageItem = pg.ImageItem()

            gradItem = widgets.overlayLabelsGradientWidget(
                imageItem, selectActionGroup, segmEndname
            )
            gradItem.hide()
            gradItem.drawModeActionGroup.triggered.connect(
                self.overlayLabelsDrawModeToggled
            )
            self.mainLayout.addWidget(gradItem, 0, 0)

            contoursItem = pg.ScatterPlotItem()
            contoursItem.setData(
                [], [], symbol='s', pxMode=False, size=1,
                brush=pg.mkBrush(color=(255,0,0,150)),
                pen=pg.mkPen(width=1, color='r'), tip=None
            )

            items = (imageItem, contoursItem, gradItem)
            self.overlayLabelsItems[segmEndname] = items
    
    def addOverlayLabelsToggled(self, checked):
        name = self.sender().text()
        if checked:
            gradItem = self.overlayLabelsItems[name][-1]
            drawMode = gradItem.drawModeActionGroup.checkedAction().text()
            self.drawModeOverlayLabelsChannels[name] = drawMode
        else:
            self.drawModeOverlayLabelsChannels.pop(name)
        self.setOverlayLabelsItems()
    
    def overlayLabelsDrawModeToggled(self, action):
        segmEndname = action.segmEndname
        drawMode = action.text()
        if segmEndname in self.drawModeOverlayLabelsChannels:
            self.drawModeOverlayLabelsChannels[segmEndname] = drawMode
            self.setOverlayLabelsItems()
    
    def overlayChannelToggled(self, checked):
        # Action toggled from overlayButton context menu
        channelName = self.sender().text()
        if checked:
            posData = self.data[self.pos_i]
            if channelName not in posData.loadedFluoChannels:
                self.loadOverlayData([channelName], addToExisting=True)
            
            self.checkedOverlayChannels.add(channelName)    
        else:
            self.checkedOverlayChannels.remove(channelName)
            imageItem = self.overlayLayersItems[channelName][0]
            imageItem.clear()
        self.setOverlayItemsVisible()
        self.updateAllImages()

    @exception_handler
    def loadDataWorkerDataIntegrityWarning(self, pos_foldername):
        err_msg = (
            'WARNING: Segmentation mask file ("..._segm.npz") not found. '
            'You could run segmentation module first.'
        )
        self.workerProgress(err_msg, 'INFO')
        self.titleLabel.setText(err_msg, color='r')
        abort = False
        msg = widgets.myMessageBox(parent=self)
        warn_msg = html_utils.paragraph(f"""
            The folder {pos_foldername} <b>does not contain a
            pre-computed segmentation mask</b>.<br><br>
            You can continue with a blank mask or cancel and
            pre-compute the mask with the segmentation module.<br><br>
            Do you want to continue?
        """)
        msg.setIcon(iconName='SP_MessageBoxWarning')
        msg.setWindowTitle('Segmentation file not found')
        msg.addText(warn_msg)
        msg.addButton('Ok')
        continueWithBlankSegm = msg.addButton(' Cancel ')
        msg.show(block=True)
        if continueWithBlankSegm == msg.clickedButton:
            abort = True
        self.loadDataWorker.abort = abort
        self.loadDataWaitCond.wakeAll()

    def warnMemoryNotSufficient(self, total_ram, available_ram, required_ram):
        total_ram = myutils._bytes_to_GB(total_ram)
        available_ram = myutils._bytes_to_GB(available_ram)
        required_ram = myutils._bytes_to_GB(required_ram)
        required_perc = round(100*required_ram/available_ram)
        msg = widgets.myMessageBox()
        txt = html_utils.paragraph(f"""
            The total amount of data that you requested to load is about
            <b>{required_ram:.2f} GB</b> ({required_perc}% of the available memory)
            but there are only <b>{available_ram:.2f} GB</b> available.<br><br>
            For <b>optimal operation</b>, we recommend loading <b>maximum 30%</b>
            of the available memory. To do so, try to close open apps to
            free up some memory. Another option is to crop the images
            using the data prep module.<br><br>
            If you choose to continue, the <b>system might freeze</b>
            or your OS could simply kill the process.<br><br>
            What do you want to do?
        """)
        cancelButton, continueButton = msg.warning(
            self, 'Memory not sufficient', txt,
            buttonsTexts=('Cancel', 'Continue anyway')
        )
        if msg.clickedButton == continueButton:
            # Disable autosaving since it would keep a copy of the data and 
            # we cannot afford it with low memory
            self.autoSaveToggle.setChecked(False)
            return True
        else:
            return False

    def checkMemoryRequirements(self, required_ram):
        memory = psutil.virtual_memory()
        total_ram = memory.total
        available_ram = memory.available
        if required_ram/available_ram > 0.3:
            proceed = self.warnMemoryNotSufficient(
                total_ram, available_ram, required_ram
            )
            return proceed
        else:
            return True

    def criticalImgPathNotFound(self, images_path):
        self.logger.info(
            'The following folder does not contain valid image files: '
            f'"{images_path}"\n\n'
            'Check that all the positions loaded contain the same channel name. '
            'Make sure to double check for spelling mistakes or types in the '
            'channel names.'
        )
        msg = widgets.myMessageBox()
        msg.addShowInFileManagerButton(images_path)
        err_msg = html_utils.paragraph(f"""
            The folder<br><br>
            <code>{images_path}</code><br><br>
            <b>does not contain any valid image file!</b><br><br>
            Valid file formats are .h5, .tif, _aligned.h5, _aligned.npz.
        """)
        okButton = msg.critical(
            self, 'No valid files found!', err_msg, buttonsTexts=('Ok',)
        )
    
    def initRealTimeTracker(self):
        for rtTrackerAction in self.trackingAlgosGroup.actions():
            if rtTrackerAction.isChecked():
                break
        
        rtTracker = rtTrackerAction.text()
        if rtTracker == 'Cell-ACDC':
            return
        if rtTracker == 'YeaZ':
            return
        
        self.logger.info(f'Initializing {rtTracker} tracker...')
        posData = self.data[self.pos_i]
        self.realTimeTracker, self.track_frame_params = myutils.import_tracker(
            posData, rtTracker, qparent=self
        )
        self.logger.info(f'{rtTracker} tracker successfully initialized.')
        if 'image_channel_name' in self.track_params:
            # Remove the channel name since it was already loaded in import_tracker
            del self.track_params['image_channel_name']

    def initFluoData(self):
        if len(self.ch_names) <= 1:
            return
        
        if 'ask_load_fluo_at_init' in self.df_settings.index:
            if self.df_settings.at['ask_load_fluo_at_init', 'value'] == 'No':
                return   
        msg = widgets.myMessageBox(allowClose=False)
        txt = (
            'Do you also want to <b>load fluorescence images?</b><br>'
            'You can load <b>as many channels as you want</b>.<br><br>'
            'If you load fluorescence images then the software will '
            '<b>calculate metrics</b> for each loaded fluorescence channel '
            'such as min, max, mean, quantiles, etc. '
            'of each segmented object.<br><br>'
            'NOTE: You can always load them later from the menu '
            '<code>File --> Load fluorescence images...</code> or when you set '
            'measurements from the menu '
            '<code>Measurements --> Set measurements...</code>'
        )
        msg.addDoNotShowAgainCheckbox(text="Don't ask again")
        no, yes = msg.question(
            self, 'Load fluorescence images?', html_utils.paragraph(txt),
            buttonsTexts=('No', 'Yes')
        )
        if msg.doNotShowAgainCheckbox.isChecked():
            self.df_settings.at['ask_load_fluo_at_init', 'value'] = 'No'
            self.df_settings.to_csv(self.settings_csv_path)
        if msg.clickedButton == yes:
            self.loadFluo_cb(None)
        self.AutoPilotProfile.storeClickMessageBox(
            'Load fluorescence images?', msg.clickedButton.text()
        )

    def getPathFromChName(self, chName, posData):
        ls = myutils.listdir(posData.images_path)
        endnames = {f[len(posData.basename):]:f for f in ls}
        validEnds = ['_aligned.npz', '_aligned.h5', '.h5', '.tif', '.npz']
        for end in validEnds:
            files = [
                filename for endname, filename in endnames.items()
                if endname == f'{chName}{end}'
            ]
            if files:
                filename = files[0]
                break
        else:
            self.criticalFluoChannelNotFound(chName, posData)
            self.app.restoreOverrideCursor()
            return None, None

        fluo_path = os.path.join(posData.images_path, filename)
        filename, _ = os.path.splitext(filename)
        return fluo_path, filename
    
    def loadPosTriggered(self):
        if not self.dataIsLoaded:
            return
        
        self.startAutomaticLoadingPos()
    
    def startAutomaticLoadingPos(self):
        self.AutoPilot = autopilot.AutoPilot(self)
        self.AutoPilot.execLoadPos()
    
    def stopAutomaticLoadingPos(self):
        if self.AutoPilot is None:
            return
        
        if self.AutoPilot.timer.isActive():
            self.AutoPilot.timer.stop()
        self.AutoPilot = None

    def loadFluo_cb(self, checked=True, fluo_channels=None):
        if fluo_channels is None:
            posData = self.data[self.pos_i]
            ch_names = [
                ch for ch in self.ch_names if ch != self.user_ch_name
                and ch not in posData.loadedFluoChannels
            ]
            if not ch_names:
                msg = widgets.myMessageBox()
                txt = html_utils.paragraph(
                    'You already <b>loaded ALL channels</b>.<br><br>'
                    'To <b>change the overlaid channel</b> '
                    '<b>right-click</b> on the overlay button.'
                )
                msg.information(self, 'All channels are loaded', txt)
                return False
            selectFluo = widgets.QDialogListbox(
                'Select channel to load',
                'Select channel names to load:\n',
                ch_names, multiSelection=True, parent=self
            )
            selectFluo.exec_()

            if selectFluo.cancel:
                return False

            fluo_channels = selectFluo.selectedItemsText
            self.AutoPilotProfile.storeLoadedFluoChannels(fluo_channels)

        for p, posData in enumerate(self.data):
            # posData.ol_data = None
            for fluo_ch in fluo_channels:
                fluo_path, filename = self.getPathFromChName(fluo_ch, posData)
                if fluo_path is None:
                    self.criticalFluoChannelNotFound(fluo_ch, posData)
                    return False
                fluo_data, bkgrData = self.load_fluo_data(fluo_path)
                if fluo_data is None:
                    return False
                posData.loadedFluoChannels.add(fluo_ch)

                if posData.SizeT == 1:
                    fluo_data = fluo_data[np.newaxis]

                posData.fluo_data_dict[filename] = fluo_data
                posData.fluo_bkgrData_dict[filename] = bkgrData
                posData.ol_data_dict[filename] = fluo_data.copy()
                
        self.overlayButton.setStyleSheet('background-color: #A7FAC7')
        self.guiTabControl.addChannels([
            posData.user_ch_name, *posData.loadedFluoChannels
        ])
        return True
    
    def labelRoiCancelled(self):
        self.labelRoiRunning = False
        self.app.restoreOverrideCursor() 
        self.labelRoiItem.setPos((0,0))
        self.labelRoiItem.setSize((0,0))
        self.freeRoiItem.clear()
        self.logger.info('Magic labeller process cancelled.')

    def labelRoiCheckStartStopFrame(self):
        if not self.labelRoiTrangeCheckbox.isChecked():
            return True
        
        start_n = self.labelRoiStartFrameNoSpinbox.value()
        stop_n = self.labelRoiStopFrameNoSpinbox.value()
        if start_n <= stop_n:
            return True
        
        self.blinker = qutils.QControlBlink(self.labelRoiStopFrameNoSpinbox)
        self.blinker.start()
        msg = widgets.myMessageBox()
        txt = html_utils.paragraph("""
            Stop frame number is less than start frame number!<br><br>
            What do you want to do?
        """)
        msg.warning(
            self, 'Stop frame number lower than start', txt, 
            buttonsTexts=('Cancel', 'Segment only current frame')
        )
        if msg.cancel:
            return False
        
        posData = self.data[self.pos_i]
        self.labelRoiStartFrameNoSpinbox.setValue(posData.frame_i+1)
        self.labelRoiStopFrameNoSpinbox.setValue(posData.frame_i+1)
        

    
    def getSecondChannelData(self):
        if self.secondChannelName is None:
            return

        posData = self.data[self.pos_i]

        fluo_ch = self.secondChannelName
        fluo_path, filename = self.getPathFromChName(fluo_ch, posData)
        if filename in posData.fluo_data_dict:
            fluo_data = posData.fluo_data_dict[filename]
        else:
            fluo_data, bkgrData = self.load_fluo_data(fluo_path)
            posData.fluo_data_dict[filename] = fluo_data
            posData.fluo_bkgrData_dict[filename] = bkgrData
        
        if self.labelRoiTrangeCheckbox.isChecked():
            start_frame_i = self.labelRoiStartFrameNoSpinbox.value()-1
            stop_frame_n = self.labelRoiStopFrameNoSpinbox.value()
            tRangeLen = stop_frame_n-start_frame_i
        else:
            tRangeLen = 1

        if tRangeLen > 1:
            tRange = (start_frame_i, stop_frame_n)
            fluo_img_data = fluo_data[start_frame_i:stop_frame_n]
            if self.isSegm3D:
                return fluo_img_data
            else:
                T, Z, Y, X = fluo_img_data.shape
                secondChannelData = np.zeros((T, Y, X), dtype=fluo_img_data.dtype)
                for frame_i, fluo_img in enumerate(fluo_img_data):
                    secondChannelData[frame_i] = self.get_2Dimg_from_3D(
                        fluo_img_data, frame_i=frame_i
                    )
        else:
            fluo_img_data = fluo_data[posData.frame_i]
            if self.isSegm3D:
                return fluo_img_data
            else:
                return self.get_2Dimg_from_3D(fluo_img_data)
    
    def addActionsLutItemContextMenu(self, lutItem):      
        lutItem.gradient.menu.addSection('Visible channels: ')
        for action in self.overlayContextMenu.actions():
            if action.isSeparator():
                continue
            lutItem.gradient.menu.addAction(action)
        lutItem.gradient.menu.addSeparator()

        annotationMenu = lutItem.gradient.menu.addMenu('Annotations settings')
        ID_menu = annotationMenu.addMenu('IDs')
        self.annotSettingsIDmenu = QActionGroup(annotationMenu)
        labID_action = QAction("Show label's ID")
        labID_action.setCheckable(True)
        labID_action.setChecked(True)
        labID_action.toggled.connect(self.annotLabelIDtreeToggled)
        treeID_action = QAction("Show tree's ID")
        treeID_action.setCheckable(True)
        treeID_action.toggled.connect(self.annotLabelIDtreeToggled)
        self.annotSettingsIDmenu.addAction(labID_action)
        self.annotSettingsIDmenu.addAction(treeID_action)
        ID_menu.addAction(labID_action)
        ID_menu.addAction(treeID_action)

        ID_menu = annotationMenu.addMenu('Generation number')
        self.annotSettingsGenNumMenu = QActionGroup(annotationMenu)
        gen_num_action = QAction("Show default generation number")
        gen_num_action.setCheckable(True)
        gen_num_action.setChecked(True)
        gen_num_action.toggled.connect(self.annotGenNumTreeToggled)
        tree_gen_num_action = QAction("Show tree generation number")
        tree_gen_num_action.setCheckable(True)
        tree_gen_num_action.toggled.connect(self.annotGenNumTreeToggled)
        self.annotSettingsGenNumMenu.addAction(gen_num_action)
        self.annotSettingsGenNumMenu.addAction(tree_gen_num_action)
        ID_menu.addAction(gen_num_action)
        ID_menu.addAction(tree_gen_num_action)

    def annotGenNumTreeToggled(self, checked):
        self.textAnnot[0].setGenNumTreeAnnotationsEnabled(checked)
    
    def annotLabelIDtreeToggled(self, checked):
        self.textAnnot[0].setLabelTreeAnnotationsEnabled(checked)

    def setAnnotInfoMode(self, checked):
        if checked:
            for action in self.annotSettingsIDmenu.actions():
                if action.text().find('tree') != -1:
                    self.textAnnot[0].setLabelTreeAnnotationsEnabled(True)
                    action.setChecked(True)
                    break
            for action in self.annotSettingsGenNumMenu.actions():
                if action.text().find('tree') != -1:
                    self.textAnnot[0].setGenNumTreeAnnotationsEnabled(True)
                    action.setChecked(True)
                    break
        else:
            for action in self.annotSettingsIDmenu.actions():
                if action.text().find('tree') == -1:
                    action.setChecked(False)
                    self.textAnnot[0].setLabelTreeAnnotationsEnabled(False)
                    break
            for action in self.annotSettingsGenNumMenu.actions():
                if action.text().find('tree') == -1:
                    action.setChecked(False)
                    self.textAnnot[0].setGenNumTreeAnnotationsEnabled(False)
                    break
    
    def annotOptionClicked(self, clicked=True, sender=None):
        if sender is None:
            sender = self.sender()
        # First manually set exclusive with uncheckable
        clickedIDs = sender == self.annotIDsCheckbox
        clickedCca = sender == self.annotCcaInfoCheckbox
        clickedMBline = sender == self.drawMothBudLinesCheckbox
        if self.annotIDsCheckbox.isChecked() and clickedIDs:
            if self.annotCcaInfoCheckbox.isChecked():
                self.annotCcaInfoCheckbox.setChecked(False)
            if self.drawMothBudLinesCheckbox.isChecked():
                self.drawMothBudLinesCheckbox.setChecked(False)
        
        if self.annotCcaInfoCheckbox.isChecked() and clickedCca:
            if self.annotIDsCheckbox.isChecked():
                self.annotIDsCheckbox.setChecked(False)
            if self.drawMothBudLinesCheckbox.isChecked():
                self.drawMothBudLinesCheckbox.setChecked(False)
        
        if self.drawMothBudLinesCheckbox.isChecked() and clickedMBline:
            if self.annotIDsCheckbox.isChecked():
                self.annotIDsCheckbox.setChecked(False)
            if self.annotCcaInfoCheckbox.isChecked():
                self.annotCcaInfoCheckbox.setChecked(False)
        
        clickedCont = sender == self.annotContourCheckbox
        clickedSegm = sender == self.annotSegmMasksCheckbox
        if self.annotContourCheckbox.isChecked() and clickedCont:
            if self.annotSegmMasksCheckbox.isChecked():
                self.annotSegmMasksCheckbox.setChecked(False)
        
        if self.annotSegmMasksCheckbox.isChecked() and clickedSegm:
            if self.annotContourCheckbox.isChecked():
                self.annotContourCheckbox.setChecked(False)
        
        clickedDoNot = sender == self.drawNothingCheckbox
        if clickedDoNot:
            self.annotIDsCheckbox.setChecked(False)
            self.annotCcaInfoCheckbox.setChecked(False)
            self.annotContourCheckbox.setChecked(False)
            self.annotSegmMasksCheckbox.setChecked(False)
            self.drawMothBudLinesCheckbox.setChecked(False)
            self.annotNumZslicesCheckbox.setChecked(False)
        else:
            self.drawNothingCheckbox.setChecked(False)
        
        if sender == self.annotNumZslicesCheckbox:
            self.annotIDsCheckbox.setChecked(True)
            self.drawNothingCheckbox.setChecked(False)
        
        self.setDrawAnnotComboboxText()

    def annotOptionClickedRight(self, clicked=True, sender=None):
        if sender is None:
            sender = self.sender()
        # First manually set exclusive with uncheckable
        clickedIDs = sender == self.annotIDsCheckboxRight
        clickedCca = sender == self.annotCcaInfoCheckboxRight
        clickedMBline = sender == self.drawMothBudLinesCheckboxRight
        if self.annotIDsCheckboxRight.isChecked() and clickedIDs:
            if self.annotCcaInfoCheckboxRight.isChecked():
                self.annotCcaInfoCheckboxRight.setChecked(False)
            if self.drawMothBudLinesCheckboxRight.isChecked():
                self.drawMothBudLinesCheckboxRight.setChecked(False)
        
        if self.annotCcaInfoCheckboxRight.isChecked() and clickedCca:
            if self.annotIDsCheckboxRight.isChecked():
                self.annotIDsCheckboxRight.setChecked(False)
            if self.drawMothBudLinesCheckboxRight.isChecked():
                self.drawMothBudLinesCheckboxRight.setChecked(False)
        
        if self.drawMothBudLinesCheckboxRight.isChecked() and clickedMBline:
            if self.annotIDsCheckboxRight.isChecked():
                self.annotIDsCheckboxRight.setChecked(False)
            if self.annotCcaInfoCheckboxRight.isChecked():
                self.annotCcaInfoCheckboxRight.setChecked(False)
        
        clickedCont = sender == self.annotContourCheckboxRight
        clickedSegm = sender == self.annotSegmMasksCheckboxRight
        if self.annotContourCheckboxRight.isChecked() and clickedCont:
            if self.annotSegmMasksCheckboxRight.isChecked():
                self.annotSegmMasksCheckboxRight.setChecked(False)
        
        if self.annotSegmMasksCheckboxRight.isChecked() and clickedSegm:
            if self.annotContourCheckboxRight.isChecked():
                self.annotContourCheckboxRight.setChecked(False)
        
        clickedDoNot = sender == self.drawNothingCheckboxRight
        if clickedDoNot:
            self.annotIDsCheckboxRight.setChecked(False)
            self.annotCcaInfoCheckboxRight.setChecked(False)
            self.annotContourCheckboxRight.setChecked(False)
            self.annotSegmMasksCheckboxRight.setChecked(False)
            self.drawMothBudLinesCheckboxRight.setChecked(False)
            self.annotNumZslicesCheckboxRight.setChecked(False)
        else:
            self.drawNothingCheckboxRight.setChecked(False)
        
        if sender == self.annotNumZslicesCheckboxRight:
            self.annotIDsCheckboxRight.setChecked(True)
            self.drawNothingCheckboxRight.setChecked(False)

        self.setDrawAnnotComboboxTextRight()

    def setDrawAnnotComboboxText(self):
        if self.annotIDsCheckbox.isChecked():
            if self.annotContourCheckbox.isChecked():
                t = 'Draw IDs and contours'
            elif self.annotSegmMasksCheckbox.isChecked():
                t = 'Draw IDs and overlay segm. masks'
            else:
                t = 'Draw only IDs'
        
        elif self.annotCcaInfoCheckbox.isChecked():
            if self.annotContourCheckbox.isChecked():
                t = 'Draw cell cycle info and contours'
            elif self.annotSegmMasksCheckbox.isChecked():
                t = 'Draw cell cycle info and overlay segm. masks'
            else:
                t = 'Draw only cell cycle info'
        
        elif self.annotSegmMasksCheckbox.isChecked():
            t = 'Draw only overlay segm. masks'

        elif self.annotContourCheckbox.isChecked():
            t = 'Draw only contours'
        
        elif self.drawMothBudLinesCheckbox.isChecked():
            t = 'Draw only mother-bud lines'
        
        elif self.drawNothingCheckbox.isChecked():
            t = 'Draw nothing'
        else:
            t = 'Draw nothing'

        if t == self.drawIDsContComboBox.currentText():
            self.drawIDsContComboBox_cb(0)
        
        self.drawIDsContComboBox.setCurrentText(t)

    def setDrawAnnotComboboxTextRight(self):
        if self.annotIDsCheckboxRight.isChecked():
            if self.annotContourCheckboxRight.isChecked():
                t = 'Draw IDs and contours'
            elif self.annotSegmMasksCheckboxRight.isChecked():
                t = 'Draw IDs and overlay segm. masks'
            else:
                t = 'Draw only IDs'
        
        elif self.annotCcaInfoCheckboxRight.isChecked():
            if self.annotContourCheckboxRight.isChecked():
                t = 'Draw cell cycle info and contours'
            elif self.annotSegmMasksCheckboxRight.isChecked():
                t = 'Draw cell cycle info and overlay segm. masks'
            else:
                t = 'Draw only cell cycle info'
        
        elif self.annotSegmMasksCheckboxRight.isChecked():
            t = 'Draw only overlay segm. masks'

        elif self.annotContourCheckboxRight.isChecked():
            t = 'Draw only contours'
        
        elif self.drawMothBudLinesCheckboxRight.isChecked():
            t = 'Draw only mother-bud lines'
        
        elif self.drawNothingCheckboxRight.isChecked():
            t = 'Draw nothing'
        else:
            t = 'Draw nothing'

        if t == self.annotateRightHowCombobox.currentText():
            self.annotateRightHowCombobox_cb(0)
        self.annotateRightHowCombobox.setCurrentText(t)
    
    def getOverlayItems(self, channelName):
        imageItem = pg.ImageItem()
        imageItem.setOpacity(0.5)

        lutItem = widgets.myHistogramLUTitem(
            parent=self, name='image', axisLabel=channelName
        )
        
        lutItem.restoreState(self.df_settings)
        lutItem.setImageItem(imageItem)
        lutItem.vb.raiseContextMenu = lambda x: None
        initColor = self.overlayColors[channelName]
        self.initColormapOverlayLayerItem(initColor, lutItem)
        lutItem.addOverlayColorButton(initColor, channelName)
        lutItem.initColor = initColor
        lutItem.hide()

        lutItem.overlayColorButton.sigColorChanging.connect(
            self.changeOverlayColor
        )
        lutItem.overlayColorButton.sigColorChanged.connect(
            self.saveOverlayColor
        )

        lutItem.invertBwAction.toggled.connect(self.setCheckedInvertBW)

        lutItem.contoursColorButton.disconnect() 
        lutItem.contoursColorButton.clicked.connect(
            self.imgGrad.contoursColorButton.click
        )
        for act in lutItem.contLineWightActionGroup.actions():
            act.toggled.connect(self.contLineWeightToggled)
        
        lutItem.mothBudLineColorButton.disconnect() 
        lutItem.mothBudLineColorButton.clicked.connect(
            self.imgGrad.mothBudLineColorButton.click
        )
        for act in lutItem.mothBudLineWightActionGroup.actions():
            act.toggled.connect(self.mothBudLineWeightToggled)

        lutItem.textColorButton.disconnect()
        lutItem.textColorButton.clicked.connect(
            self.editTextIDsColorAction.trigger
        )

        lutItem.defaultSettingsAction.triggered.connect(
            self.restoreDefaultSettings
        )
        lutItem.labelsAlphaSlider.valueChanged.connect(
            self.setValueLabelsAlphaSlider
        )

        self.addActionsLutItemContextMenu(lutItem)

        alphaScrollBar = self.addAlphaScrollbar(channelName, imageItem)

        return imageItem, lutItem, alphaScrollBar
    
    def addAlphaScrollbar(self, channelName, imageItem):
        alphaScrollBar = widgets.ScrollBar(Qt.Horizontal)
        
        label = QLabel(f'Alpha {channelName}')
        label.setFont(_font)
        label.hide()
        alphaScrollBar.imageItem = imageItem
        alphaScrollBar.label = label
        alphaScrollBar.setFixedHeight(self.h)
        alphaScrollBar.hide()
        alphaScrollBar.setMinimum(0)
        alphaScrollBar.setMaximum(40)
        alphaScrollBar.setValue(20)
        alphaScrollBar.setToolTip(
            f'Control the alpha value of the overlaid channel {channelName}.\n'
            'alpha=0 results in NO overlay,\n'
            'alpha=1 results in only fluorescence data visible'
        )
        self.bottomLeftLayout.addWidget(
            alphaScrollBar.label, self.alphaScrollbarRow, 0, 
            alignment=Qt.AlignRight
        )
        self.bottomLeftLayout.addWidget(
            alphaScrollBar, self.alphaScrollbarRow, 1, 1, 2
        )

        alphaScrollBar.valueChanged.connect(self.setOpacityOverlayLayersItems)

        self.alphaScrollbarRow += 1
        return alphaScrollBar
    
    def setValueLabelsAlphaSlider(self, value):
        self.imgGrad.labelsAlphaSlider.setValue(value)
        self.updateLabelsAlpha(value)
    
    def setOverlayLabelsItemsVisible(self, checked):  
        for _segmEndname, drawMode in self.drawModeOverlayLabelsChannels.items():
            items = self.overlayLabelsItems[_segmEndname]
            gradItem = items[-1]
            gradItem.hide()
        
        if checked:
            segmEndname = self.sender().text()
            gradItem = self.overlayLabelsItems[segmEndname][-1]
            gradItem.show()
    
    def setRetainSizePolicyLutItems(self):
        if self.retainSizeLutItems:
            return
        for channel, items in self.overlayLayersItems.items():
            _, lutItem, alphaSB = items
            myutils.setRetainSizePolicy(lutItem, retain=True)
        QTimer.singleShot(300, self.autoRange)

    def setOverlayItemsVisible(self):
        for channel, items in self.overlayLayersItems.items():
            _, lutItem, alphaSB = items
            if not self.overlayButton.isChecked():
                lutItem.hide()
                alphaSB.hide()
                alphaSB.label.hide()
            elif channel in self.checkedOverlayChannels:
                lutItem.show()
                alphaSB.show()
                alphaSB.label.show()
            else:
                lutItem.hide()
                alphaSB.hide()
                alphaSB.label.hide()

    def initColormapOverlayLayerItem(self, foregrColor, lutItem):
        if self.invertBwAction.isChecked():
            bkgrColor = (255,255,255,255)
        else:
            bkgrColor = (0,0,0,255)
        gradient = colors.get_pg_gradient((bkgrColor, foregrColor))
        lutItem.setGradient(gradient)
    
    def setOpacityOverlayLayersItems(self, value, imageItem=None):
        if imageItem is None:
            imageItem = self.sender().imageItem
            alpha = value/self.sender().maximum()
        else:
            alpha = value
        imageItem.setOpacity(alpha)
        
    def showInExplorer_cb(self):
        posData = self.data[self.pos_i]
        path = posData.images_path
        myutils.showInExplorer(path)

    def zSliceAbsent(self, filename, posData):
        self.app.restoreOverrideCursor()
        SizeZ = posData.SizeZ
        chNames = posData.chNames
        filenamesPresent = posData.segmInfo_df.index.get_level_values(0).unique()
        chNamesPresent = [
            ch for ch in chNames
            for file in filenamesPresent
            if file.endswith(ch) or file.endswith(f'{ch}_aligned')
        ]
        win = apps.QDialogZsliceAbsent(filename, SizeZ, chNamesPresent)
        win.exec_()
        if win.cancel:
            self.worker.abort = True
            self.waitCond.wakeAll()
            return
        if win.useMiddleSlice:
            user_ch_name = filename[len(posData.basename):]
            for posData in self.data:
                _, filename = self.getPathFromChName(user_ch_name, posData)
                df = myutils.getDefault_SegmInfo_df(posData, filename)
                posData.segmInfo_df = pd.concat([df, posData.segmInfo_df])
                unique_idx = ~posData.segmInfo_df.index.duplicated()
                posData.segmInfo_df = posData.segmInfo_df[unique_idx]
                posData.segmInfo_df.to_csv(posData.segmInfo_df_csv_path)
        elif win.useSameAsCh:
            user_ch_name = filename[len(posData.basename):]
            for posData in self.data:
                _, srcFilename = self.getPathFromChName(
                    win.selectedChannel, posData
                )
                cellacdc_df = posData.segmInfo_df.loc[srcFilename].copy()
                _, dstFilename = self.getPathFromChName(user_ch_name, posData)
                if dstFilename is None:
                    self.worker.abort = True
                    self.waitCond.wakeAll()
                    return
                dst_df = myutils.getDefault_SegmInfo_df(posData, dstFilename)
                for z_info in cellacdc_df.itertuples():
                    frame_i = z_info.Index
                    zProjHow = z_info.which_z_proj
                    if zProjHow == 'single z-slice':
                        src_idx = (srcFilename, frame_i)
                        if posData.segmInfo_df.at[src_idx, 'resegmented_in_gui']:
                            col = 'z_slice_used_gui'
                        else:
                            col = 'z_slice_used_dataPrep'
                        z_slice = posData.segmInfo_df.at[src_idx, col]
                        dst_idx = (dstFilename, frame_i)
                        dst_df.at[dst_idx, 'z_slice_used_dataPrep'] = z_slice
                        dst_df.at[dst_idx, 'z_slice_used_gui'] = z_slice
                posData.segmInfo_df = pd.concat([dst_df, posData.segmInfo_df])
                unique_idx = ~posData.segmInfo_df.index.duplicated()
                posData.segmInfo_df = posData.segmInfo_df[unique_idx]
                posData.segmInfo_df.to_csv(posData.segmInfo_df_csv_path)
        elif win.runDataPrep:
            user_ch_file_paths = []
            user_ch_name = filename[len(self.data[self.pos_i].basename):]
            for _posData in self.data:
                user_ch_path, _ = self.getPathFromChName(user_ch_name, _posData)
                if user_ch_path is None:
                    self.worker.abort = True
                    self.waitCond.wakeAll()
                    return
                user_ch_file_paths.append(user_ch_path)
                exp_path = os.path.dirname(_posData.pos_path)

            dataPrepWin = dataPrep.dataPrepWin()
            dataPrepWin.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
            dataPrepWin.titleText = (
            """
            Select z-slice (or projection) for each frame/position.<br>
            Once happy, close the window.
            """)
            dataPrepWin.show()
            dataPrepWin.initLoading()
            dataPrepWin.SizeT = self.data[0].SizeT
            dataPrepWin.SizeZ = self.data[0].SizeZ
            dataPrepWin.metadataAlreadyAsked = True
            self.logger.info(f'Loading channel {user_ch_name} data...')
            dataPrepWin.loadFiles(
                exp_path, user_ch_file_paths, user_ch_name
            )
            dataPrepWin.startAction.setDisabled(True)
            dataPrepWin.onlySelectingZslice = True

            loop = QEventLoop(self)
            dataPrepWin.loop = loop
            loop.exec_()

        self.waitCond.wakeAll()

    def showSetMeasurements(self, checked=False):
        try:
            df_favourite_funcs = pd.read_csv(favourite_func_metrics_csv_path)
            favourite_funcs = df_favourite_funcs['favourite_func_name'].to_list()
        except Exception as e:
            favourite_funcs = None

        posData = self.data[self.pos_i]
        allPos_acdc_df_cols = set()
        for _posData in self.data:
            for frame_i, data_dict in enumerate(_posData.allData_li):
                acdc_df = data_dict['acdc_df']
                if acdc_df is None:
                    continue
                
                allPos_acdc_df_cols.update(acdc_df.columns)
        loadedChNames = posData.setLoadedChannelNames(returnList=True)
        loadedChNames.insert(0, self.user_ch_name)
        notLoadedChNames = [c for c in self.ch_names if c not in loadedChNames]
        self.notLoadedChNames = notLoadedChNames
        self.measurementsWin = apps.setMeasurementsDialog(
            loadedChNames, notLoadedChNames, posData.SizeZ > 1, self.isSegm3D,
            favourite_funcs=favourite_funcs, 
            allPos_acdc_df_cols=list(allPos_acdc_df_cols),
            acdc_df_path=posData.images_path, posData=posData,
            addCombineMetricCallback=self.addCombineMetric,
            allPosData=self.data, parent=self
        )
        self.measurementsWin.sigClosed.connect(self.setMeasurements)
        self.measurementsWin.show()

    def setMeasurements(self):
        posData = self.data[self.pos_i]
        if self.measurementsWin.delExistingCols:
            self.logger.info('Removing existing unchecked measurements...')
            delCols = self.measurementsWin.existingUncheckedColnames
            delRps = self.measurementsWin.existingUncheckedRps
            delCols_format = [f'  *  {colname}' for colname in delCols]
            delRps_format = [f'  *  {colname}' for colname in delRps]
            delCols_format.extend(delRps_format)
            delCols_format = '\n'.join(delCols_format)
            self.logger.info(delCols_format)
            for _posData in self.data:
                for frame_i, data_dict in enumerate(_posData.allData_li):
                    acdc_df = data_dict['acdc_df']
                    if acdc_df is None:
                        continue
                    
                    acdc_df = acdc_df.drop(columns=delCols, errors='ignore')
                    for col_rp in delRps:
                        drop_df_rp = acdc_df.filter(regex=fr'{col_rp}.*', axis=1)
                        drop_cols_rp = drop_df_rp.columns
                        acdc_df = acdc_df.drop(columns=drop_cols_rp, errors='ignore')
                    _posData.allData_li[frame_i]['acdc_df'] = acdc_df
        self.logger.info('Setting measurements...')
        self._setMetrics(self.measurementsWin)
        self.logger.info('Metrics successfully set.')
        self.measurementsWin = None

    def _setMetrics(self, measurementsWin):
        self.chNamesToSkip = []
        self.metricsToSkip = {chName:[] for chName in self.ch_names}
        self.metricsToSave = {chName:[] for chName in self.ch_names}
        
        favourite_funcs = set()
        last_selected_groupboxes_measurements = load.read_last_selected_gb_meas(
            logger_func=self.logger.info
        )
        refChannel = measurementsWin.chNameGroupboxes[0].chName
        if refChannel not in last_selected_groupboxes_measurements:
            last_selected_groupboxes_measurements[refChannel] = []
        # Remove unchecked metrics and load checked not loaded channels
        for chNameGroupbox in measurementsWin.chNameGroupboxes:
            chName = chNameGroupbox.chName
            if not chNameGroupbox.isChecked():
                # Skip entire channel
                self.chNamesToSkip.append(chName)
            else:
                last_selected_groupboxes_measurements[refChannel].append(
                    chNameGroupbox.title()
                )
                if chName in self.notLoadedChNames:
                    success = self.loadFluo_cb(fluo_channels=[chName])
                    if not success:
                        continue
                for checkBox in chNameGroupbox.checkBoxes:
                    colname = checkBox.text()
                    if not checkBox.isChecked():
                        self.metricsToSkip[chName].append(colname)
                    else:
                        self.metricsToSave[chName].append(colname)
                        func_name = colname[len(chName):]
                        favourite_funcs.add(func_name)

        if not measurementsWin.sizeMetricsQGBox.isChecked():
            self.sizeMetricsToSave = []
        else:
            self.sizeMetricsToSave = []
            title = measurementsWin.sizeMetricsQGBox.title()
            last_selected_groupboxes_measurements[refChannel].append(title)
            for checkBox in measurementsWin.sizeMetricsQGBox.checkBoxes:
                if checkBox.isChecked():
                    self.sizeMetricsToSave.append(checkBox.text())
                    favourite_funcs.add(checkBox.text())

        if not measurementsWin.regionPropsQGBox.isChecked():
            self.regionPropsToSave = ()
        else:
            self.regionPropsToSave = []
            title = measurementsWin.regionPropsQGBox.title()
            last_selected_groupboxes_measurements[refChannel].append(title)
            for checkBox in measurementsWin.regionPropsQGBox.checkBoxes:
                if checkBox.isChecked():
                    self.regionPropsToSave.append(checkBox.text())
                    favourite_funcs.add(checkBox.text())
            self.regionPropsToSave = tuple(self.regionPropsToSave)

        if measurementsWin.mixedChannelsCombineMetricsQGBox is not None:
            skipAll = (
                not measurementsWin.mixedChannelsCombineMetricsQGBox.isChecked()
            )
            if not skipAll:
                title = measurementsWin.mixedChannelsCombineMetricsQGBox.title()
                last_selected_groupboxes_measurements[refChannel].append(title)
            mixedChCombineMetricsToSkip = []
            win = measurementsWin
            checkBoxes = win.mixedChannelsCombineMetricsQGBox.checkBoxes
            for checkBox in checkBoxes:
                if skipAll:
                    mixedChCombineMetricsToSkip.append(checkBox.text())
                elif not checkBox.isChecked():
                    mixedChCombineMetricsToSkip.append(checkBox.text())
                else:             
                    favourite_funcs.add(checkBox.text())
            self.mixedChCombineMetricsToSkip = tuple(mixedChCombineMetricsToSkip)

        df_favourite_funcs = pd.DataFrame(
            {'favourite_func_name': list(favourite_funcs)}
        )
        df_favourite_funcs.to_csv(favourite_func_metrics_csv_path)

        load.save_last_selected_gb_meas(last_selected_groupboxes_measurements)

    def addCustomMetric(self, checked=False):
        txt = measurements.add_metrics_instructions()
        metrics_path = measurements.metrics_path
        msg = widgets.myMessageBox()
        msg.addShowInFileManagerButton(metrics_path, 'Show example...')
        title = 'Add custom metrics instructions'
        msg.information(self, title, txt, buttonsTexts=('Ok',))

    def addCombineMetric(self):
        posData = self.data[self.pos_i]
        isZstack = posData.SizeZ > 1
        win = apps.combineMetricsEquationDialog(
            self.ch_names, isZstack, self.isSegm3D, parent=self
        )
        win.sigOk.connect(self.saveCombineMetricsToPosData)
        win.exec_()
        win.sigOk.disconnect()

    def saveCombineMetricsToPosData(self, window):
        for posData in self.data:
            equationsDict, isMixedChannels = window.getEquationsDict()
            for newColName, equation in equationsDict.items():
                posData.addEquationCombineMetrics(
                    equation, newColName, isMixedChannels
                )
                posData.saveCombineMetrics()
        
        if self.measurementsWin is None:
            return
        
        self.measurementsWinState = self.measurementsWin.state()
        self.measurementsWin.close()
        self.showSetMeasurements()
        self.measurementsWin.restoreState(self.measurementsWinState)

    def labelRoiToEndFramesTriggered(self):
        posData = self.data[self.pos_i]
        self.labelRoiStopFrameNoSpinbox.setValue(posData.SizeT)
    
    def labelRoiFromCurrentFrameTriggered(self):
        posData = self.data[self.pos_i]
        self.labelRoiStartFrameNoSpinbox.setValue(posData.frame_i+1)
    
    def labelRoiViewCurrentModel(self):
        from . import config
        ini_path = os.path.join(settings_folderpath, 'last_params_segm_models.ini')
        configPars = config.ConfigParser()
        configPars.read(ini_path)
        model_name = self.labelRoiModel.model_name
        txt = f'Model: <b>{model_name}</b>'
        SECTION = f'{model_name}.init'
        txt = f'{txt}<br><br>[Initialization parameters]<br>'
        for option in configPars.options(SECTION):
            value = configPars[SECTION][option]
            param_txt = f'<i>{option}</i> = {value}<br>'
            txt = f'{txt}{param_txt}'
        
        SECTION = f'{model_name}.segment'
        txt = f'{txt}<br>[Segmentation parameters]<br>'
        for option in configPars.options(SECTION):
            value = configPars[SECTION][option]
            param_txt = f'<i>{option}</i> = {value}<br>'
            txt = f'{txt}{param_txt}'
        
        win = apps.ViewTextDialog(txt, parent=self)
        win.exec_()

    def setMetricsFunc(self):
        posData = self.data[self.pos_i]
        (metrics_func, all_metrics_names,
        custom_func_dict, total_metrics) = measurements.getMetricsFunc(posData)
        self.metrics_func = metrics_func
        self.all_metrics_names = all_metrics_names
        self.total_metrics = total_metrics
        self.custom_func_dict = custom_func_dict

    def getLastTrackedFrame(self, posData):
        last_tracked_i = 0
        for frame_i, data_dict in enumerate(posData.allData_li):
            lab = data_dict['labels']
            if lab is None:
                frame_i -= 1
                break
        if frame_i > 0:
            return frame_i
        else:
            return last_tracked_i

    def computeVolumeRegionprop(self):
        if 'cell_vol_vox' not in self.sizeMetricsToSave:
            return

        # We compute the cell volume in the main thread because calling
        # skimage.transform.rotate in a separate thread causes crashes
        # with segmentation fault on macOS. I don't know why yet.
        self.logger.info('Computing cell volume...')
        end_i = self.save_until_frame_i
        pos_iter = tqdm(self.data[:self.last_pos], ncols=100)
        for p, posData in enumerate(pos_iter):
            PhysicalSizeY = posData.PhysicalSizeY
            PhysicalSizeX = posData.PhysicalSizeX
            frame_iter = tqdm(
                posData.allData_li[:end_i+1], ncols=100, position=1, leave=False
            )
            for frame_i, data_dict in enumerate(frame_iter):
                lab = data_dict['labels']
                if lab is None:
                    break
                rp = data_dict['regionprops']
                obj_iter = tqdm(rp, ncols=100, position=2, leave=False)
                for i, obj in enumerate(obj_iter):
                    vol_vox, vol_fl = _calc_rot_vol(
                        obj, PhysicalSizeY, PhysicalSizeX
                    )
                    obj.vol_vox = vol_vox
                    obj.vol_fl = vol_fl
                posData.allData_li[frame_i]['regionprops'] = rp

    def askSaveLastVisitedSegmMode(self, isQuickSave=False):
        posData = self.data[self.pos_i]
        current_frame_i = posData.frame_i
        frame_i = 0
        last_tracked_i = 0
        self.save_until_frame_i = 0
        if self.isSnapshot:
            return True

        for frame_i, data_dict in enumerate(posData.allData_li):
            lab = data_dict['labels']
            if lab is None:
                frame_i -= 1
                break

        if isQuickSave:
            self.save_until_frame_i = frame_i
            self.last_tracked_i = frame_i
            return True

        # Ask to save last visited frame or not
        txt = html_utils.paragraph(f"""
            You visited and stored data up until frame
            number {frame_i+1}.<br><br>
            Enter <b>up to which frame number</b> you want to save data:
        """)
        lastFrameDialog = apps.QLineEditDialog(
            title='Last frame number to save', defaultTxt=str(frame_i+1),
            msg=txt, parent=self, allowedValues=(1, posData.SizeT),
            warnLastFrame=True, isInteger=True, stretchEntry=False,
            lastVisitedFrame=frame_i+1
        )
        lastFrameDialog.exec_()
        if lastFrameDialog.cancel:
            return False

        self.save_until_frame_i = lastFrameDialog.EntryID - 1
        if self.save_until_frame_i > frame_i:
            self.logger.info(
                f'Storing frames {frame_i+1}-{self.save_until_frame_i+1}...'
            )
            current_frame_i = posData.frame_i
            # User is requesting to save past the last visited frame -->
            # store data as if they were visited
            for i in range(frame_i+1, self.save_until_frame_i+1):
                posData.frame_i = i
                self.get_data()
                self.store_data(autosave=False)
            
            # Go back to current frame
            posData.frame_i = current_frame_i
            self.get_data()
        last_tracked_i = self.save_until_frame_i
        
        self.last_tracked_i = last_tracked_i
        return True

    def askSaveMetrics(self):
        txt = html_utils.paragraph(
        """
            Do you also want to <b>save the measurements</b> 
            (e.g., cell volume, mean, amount etc.)?<br><br>
            
            You can find <b>more information</b> by clicking on the 
            "Set measurements" button below <br>
            where you will be able to select which <b>measurements 
            you want to save</b>.<br><br>
            If you already set the measurements and you want to save them click "Yes".<br><br>
            
            NOTE: Saving metrics might be <b>slow</b>,
            we recommend doing it <b>only when you need it</b>.<br>
        """)
        msg = widgets.myMessageBox(parent=self, resizeButtons=False, wrapText=False)
        msg.setIcon(iconName='SP_MessageBoxQuestion')
        msg.setWindowTitle('Save measurements?')
        msg.addText(txt)
        yesButton = msg.addButton('Yes')
        noButton = msg.addButton('No')
        cancelButton = msg.addButton('Cancel')
        setMeasurementsButton = msg.addButton('Set measurements...')
        setMeasurementsButton.disconnect()
        setMeasurementsButton.clicked.connect(self.showSetMeasurements)
        msg.exec_()
        save_metrics = msg.clickedButton == yesButton
        cancel = msg.clickedButton == cancelButton or msg.clickedButton is None
        return save_metrics, cancel

    def askSaveAllPos(self):
        last_pos = 1
        ask = False
        for p, posData in enumerate(self.data):
            acdc_df = posData.allData_li[0]['acdc_df']
            if acdc_df is None:
                last_pos = p
                ask = True
                break
        else:
            last_pos = len(self.data)

        if not ask:
            # All pos have been visited, no reason to ask
            return True, len(self.data)

        last_posfoldername = self.data[last_pos-1].pos_foldername
        msg = QMessageBox(self)
        msg.setWindowTitle('Save all positions?')
        msg.setIcon(msg.Question)
        txt = html_utils.paragraph(
        f"""
            Do you want to save <b>ALL positions</b> or <b>only until
            Position_{last_pos}</b> (last visualized/corrected position)?<br>
        """)
        msg.setText(txt)
        allPosbutton =  QPushButton('Save ALL positions')
        upToLastButton = QPushButton(f'Save until {last_posfoldername}')
        msg.addButton(allPosbutton, msg.YesRole)
        msg.addButton(upToLastButton, msg.NoRole)
        msg.exec_()
        return msg.clickedButton() == allPosbutton, last_pos

    def saveMetricsCritical(self, traceback_format):
        print('\n====================================')
        self.logger.exception(traceback_format)
        print('====================================\n')
        self.logger.info('Warning: calculating metrics failed see above...')
        print('------------------------------')

        msg = widgets.myMessageBox(wrapText=False)
        err_msg = html_utils.paragraph(f"""
            Error <b>while saving metrics</b>.<br><br>
            More details below or in the terminal/console.<br><br>
            Note that the error details from this session are also saved
            in the file<br>
            {self.log_path}<br><br>
            Please <b>send the log file</b> when reporting a bug, thanks!
        """)
        msg.addShowInFileManagerButton(self.logs_path, txt='Show log file...')
        msg.setDetailedText(traceback_format, visible=True)
        msg.critical(self, 'Critical error while saving metrics', err_msg)

        self.is_error_state = True
        self.waitCond.wakeAll()

    def saveAsData(self, checked=True):
        try:
            posData = self.data[self.pos_i]
        except AttributeError:
            return

        existingEndnames = set()
        for _posData in self.data:
            segm_files = load.get_segm_files(_posData.images_path)
            _existingEndnames = load.get_existing_segm_endnames(
                _posData.basename, segm_files
            )
            existingEndnames.update(_existingEndnames)
        posData = self.data[self.pos_i]
        if posData.basename.endswith('_'):
            basename = f'{posData.basename}segm'
        else:
            basename = f'{posData.basename}_segm'
        win = apps.filenameDialog(
            basename=basename,
            hintText='Insert a <b>filename</b> for the segmentation file:<br>',
            existingNames=existingEndnames
        )
        win.exec_()
        if win.cancel:
            return

        for posData in self.data:
            posData.setFilePaths(new_endname=win.entryText)

        self.setSaturBarLabel()
        self.saveData()


    def saveDataPermissionError(self, err_msg):
        msg = QMessageBox()
        msg.critical(self, 'Permission denied', err_msg, msg.Ok)
        self.waitCond.wakeAll()

    def saveDataProgress(self, text):
        self.logger.info(text)
        self.saveWin.progressLabel.setText(text)

    def saveDataCustomMetricsCritical(self, traceback_format, func_name):
        self.logger.info('')
        _hl = '===================================='
        self.logger.info(f'{_hl}\n{traceback_format}\n{_hl}')
        self.worker.customMetricsErrors[func_name] = traceback_format
    
    def saveDataCombinedMetricsMissingColumn(self, error_msg, func_name):
        self.logger.info('')
        warning = f'[WARNING]: {error_msg}. Metric {func_name} was skipped.'
        _hl = '===================================='
        self.logger.info(f'{_hl}\n{warning}\n{_hl}')
        self.worker.customMetricsErrors[func_name] = warning
    
    def saveDataAddMetricsCritical(self, traceback_format, error_message):
        self.logger.info('')
        _hl = '===================================='
        self.logger.info(f'{_hl}\n{traceback_format}\n{_hl}')
        self.worker.addMetricsErrors[error_message] = traceback_format
    
    def saveDataRegionPropsCritical(self, traceback_format, error_message):
        self.logger.info('')
        _hl = '===================================='
        self.logger.info(f'{_hl}\n{traceback_format}\n{_hl}')
        self.worker.regionPropsErrors[error_message] = traceback_format

    def saveDataUpdateMetricsPbar(self, max, step):
        if max > 0:
            self.saveWin.metricsQPbar.setMaximum(max)
            self.saveWin.metricsQPbar.setValue(0)
        self.saveWin.metricsQPbar.setValue(
            self.saveWin.metricsQPbar.value()+step
        )

    def saveDataUpdatePbar(self, step, max=-1, exec_time=0.0):
        if max >= 0:
            self.saveWin.QPbar.setMaximum(max)
        else:
            self.saveWin.QPbar.setValue(self.saveWin.QPbar.value()+step)
            steps_left = self.saveWin.QPbar.maximum()-self.saveWin.QPbar.value()
            seconds = round(exec_time*steps_left)
            ETA = myutils.seconds_to_ETA(seconds)
            self.saveWin.ETA_label.setText(f'ETA: {ETA}')
    
    def quickSave(self):
        self.saveData(isQuickSave=True)
    
    def warnDifferentSegmChannel(
            self, loaded_channel, segm_channel_hyperparams, segmEndName
        ):
        txt = html_utils.paragraph(f"""
            You loaded the segmentation file ending with <code>_{segmEndName}.npz</code> 
            which corresponds to the channel 
            <code>{segm_channel_hyperparams}</code>.<br><br>
            However, <b>in this session you loaded the channel</b> 
            <code>{loaded_channel}</code>.<br><br>
            If you proceed with saving, the segmentation file ending with 
            <code>_{segmEndName}.npz</code> <b>will be OVERWRITTEN</b>.<br><br>
            Are you sure you want to proceed?
        """)
        msg = widgets.myMessageBox(showCentered=False, wrapText=False)
        msg.warning(
            self, 'WARNING: Potential for data loss', txt,
            buttonsTexts=('Cancel', 'Yes')
        )
        return msg.cancel

    def waitAutoSaveWorker(self, worker):
        if worker.isFinished or worker.isPaused or worker.dataQ.empty():
            self.waitAutoSaveWorkerLoop.exit()
            self.waitAutoSaveWorkerTimer.stop()
            self.setSaturBarLabel(log=False)
        
    @exception_handler
    def saveData(self, checked=False, finishedCallback=None, isQuickSave=False):
        self.store_data(autosave=False)
        self.applyDelROIs()
        self.store_data()

        # Wait autosave worker to finish
        for worker, thread in self.autoSaveActiveWorkers:
            self.logger.info('Stopping autosaving process...')
            self.statusBarLabel.setText('Stopping autosaving process...')
            worker.abort()
            self.waitAutoSaveWorkerTimer = QTimer()
            self.waitAutoSaveWorkerTimer.timeout.connect(
                partial(self.waitAutoSaveWorker, worker)
            )
            self.waitAutoSaveWorkerTimer.start(100)
            self.waitAutoSaveWorkerLoop = QEventLoop()
            self.waitAutoSaveWorkerLoop.exec_()

        self.titleLabel.setText(
            'Saving data... (check progress in the terminal)', color=self.titleColor
        )

        # Check channel name correspondence to warn
        posData = self.data[self.pos_i]
        lastSegmChannel, segmEndName = posData.getSegmentedChannelHyperparams()
        if lastSegmChannel != self.user_ch_name and lastSegmChannel:
            cancel = self.warnDifferentSegmChannel(
                self.user_ch_name, lastSegmChannel, segmEndName
            )
            if cancel:
                self.abortSavingInitialisation()
                return True
            posData.updateSegmentedChannelHyperparams(self.user_ch_name)

        self.save_metrics = False
        if not isQuickSave:
            self.save_metrics, cancel = self.askSaveMetrics()
            if cancel:
                self.abortSavingInitialisation()
                return True

        last_pos = len(self.data)
        if self.isSnapshot and not isQuickSave:
            save_Allpos, last_pos = self.askSaveAllPos()
            if save_Allpos:
                last_pos = len(self.data)
                current_pos = self.pos_i
                for p in range(len(self.data)):
                    self.pos_i = p
                    self.get_data()
                    self.store_data()

                # back to current pos
                self.pos_i = current_pos
                self.get_data()

        self.last_pos = last_pos

        if self.isSnapshot:
            self.store_data(mainThread=False)

        proceed = self.askSaveLastVisitedSegmMode(isQuickSave=isQuickSave)
        if not proceed:
            return

        mode = self.modeComboBox.currentText()
        if self.save_metrics or mode == 'Cell cycle analysis':
            self.computeVolumeRegionprop()

        infoTxt = html_utils.paragraph(
            f'Saving {self.exp_path}...<br>', font_size='14px'
        )

        self.saveWin = apps.QDialogPbar(
            parent=self, title='Saving data', infoTxt=infoTxt
        )
        self.saveWin.setFont(_font)
        # if not self.save_metrics:
        self.saveWin.metricsQPbar.hide()
        self.saveWin.progressLabel.setText('Preparing data...')
        self.saveWin.show()

        # Set up separate thread for saving and show progress bar widget
        self.mutex = QMutex()
        self.waitCond = QWaitCondition()
        self.thread = QThread()
        self.worker = saveDataWorker(self)
        self.worker.mode = mode
        self.worker.saveOnlySegm = isQuickSave

        self.worker.moveToThread(self.thread)

        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        # Custom signals
        self.worker.finished.connect(self.saveDataFinished)
        if finishedCallback is not None:
            self.worker.finished.connect(finishedCallback)
        self.worker.progress.connect(self.saveDataProgress)
        self.worker.progressBar.connect(self.saveDataUpdatePbar)
        # self.worker.metricsPbarProgress.connect(self.saveDataUpdateMetricsPbar)
        self.worker.critical.connect(self.saveDataWorkerCritical)
        self.worker.customMetricsCritical.connect(
            self.saveDataCustomMetricsCritical
        )
        self.worker.sigCombinedMetricsMissingColumn.connect(
            self.saveDataCombinedMetricsMissingColumn
        )
        self.worker.addMetricsCritical.connect(self.saveDataAddMetricsCritical)
        self.worker.regionPropsCritical.connect(
            self.saveDataRegionPropsCritical
        )
        self.worker.criticalPermissionError.connect(self.saveDataPermissionError)
        self.worker.askZsliceAbsent.connect(self.zSliceAbsent)
        self.worker.sigDebug.connect(self._workerDebug)

        self.thread.started.connect(self.worker.run)

        self.thread.start()
    
    def _workerDebug(self, stuff_to_debug):
        pass
        # from acdctools.plot import imshow
        # lab, frame_i, autoBkgr_masks = stuff_to_debug
        # autoBkgr_mask, autoBkgr_mask_proj = autoBkgr_masks
        # imshow(lab, autoBkgr_mask)
        # self.worker.waitCond.wakeAll()
    
    def changeTextResolution(self):
        mode = 'high' if self.highLowResToggle.isChecked() else 'low'
        self.logger.info(
            f'Switching to {mode} for the text annnotations...'
        )
        self.pxModeToggle.setDisabled(not self.highLowResToggle.isChecked())
        self.setAllIDs()
        posData = self.data[self.pos_i]
        allIDs = posData.allIDs
        img_shape = self.img1.image.shape
        self.textAnnot[0].changeResolution(mode, allIDs, self.ax1, img_shape)
        self.textAnnot[1].changeResolution(mode, allIDs, self.ax2, img_shape)
        self.updateAllImages()
    
    def highLoweResClicked(self, clicked=True):
        self.changeTextResolution()
    
    def autoSaveClose(self):
        for worker, thread in self.autoSaveActiveWorkers:
            worker._stop()
    
    def autoSaveToggled(self, checked):
        if not self.autoSaveActiveWorkers:
            self.gui_createAutoSaveWorker()
        
        if not self.autoSaveActiveWorkers:
            return
        
        worker, thread = self.autoSaveActiveWorkers[-1]
        worker.isAutoSaveON = checked
        # self.autoSaveClose()
        
        # if checked:
        #     self.gui_createAutoSaveWorker()
    
    def warnErrorsCustomMetrics(self):
        win = apps.ComputeMetricsErrorsDialog(
            self.worker.customMetricsErrors, self.logs_path, 
            log_type='custom_metrics', parent=self
        )
        win.exec_()
    
    def warnErrorsAddMetrics(self):
        win = apps.ComputeMetricsErrorsDialog(
            self.worker.addMetricsErrors, self.logs_path, 
            log_type='standard_metrics', parent=self
        )
        win.exec_()
    
    def warnErrorsRegionProps(self):
        win = apps.ComputeMetricsErrorsDialog(
            self.worker.regionPropsErrors, self.logs_path, 
            log_type='region_props', parent=self
        )
        win.exec_()
    
    def updateSegmDataAutoSaveWorker(self):
        # Update savedSegmData in autosave worker
        posData = self.data[self.pos_i]
        for worker, thread in self.autoSaveActiveWorkers:
            worker.savedSegmData = posData.segm_data.copy()

    def saveDataFinished(self):
        if self.saveWin.aborted or self.worker.abort:
            self.titleLabel.setText('Saving process cancelled.', color='r')
        else:
            self.titleLabel.setText('Saved!')
        self.saveWin.workerFinished = True
        self.saveWin.close()

        if not self.closeGUI:
            # Update savedSegmData in autosave worker
            self.updateSegmDataAutoSaveWorker()

        if self.worker.addMetricsErrors:
           self.warnErrorsAddMetrics()    
        if self.worker.regionPropsErrors:
            self.warnErrorsRegionProps()
        if self.worker.customMetricsErrors:
            self.warnErrorsCustomMetrics()
        
        self.checkManageVersions()
        
        if self.closeGUI:
            salute_string = myutils.get_salute_string()
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph(
                'Data <b>saved!</b>. The GUI will now close.<br><br>'
                f'{salute_string}'
            )
            msg.information(self, 'Data saved', txt)
            self.close()

    def copyContent(self):
        pass

    def pasteContent(self):
        pass

    def cutContent(self):
        pass
    
    def showAbout(self):
        self.aboutWin = about.QDialogAbout(parent=self)
        self.aboutWin.show()

    def showTipsAndTricks(self):
        self.welcomeWin = welcome.welcomeWin()
        self.welcomeWin.showAndSetSize()
        self.welcomeWin.showPage(self.welcomeWin.quickStartItem)

    def about(self):
        pass

    def openRecentFile(self, path):
        self.logger.info(f'Opening recent folder: {path}')
        self.openFolder(exp_path=path)
    
    def _waitCloseAutoSaveWorker(self):
        didWorkersFinished = [True]
        for worker, thread in self.autoSaveActiveWorkers:
            if worker.isFinished:
                didWorkersFinished.append(True)
            else:
                didWorkersFinished.append(False)
        if all(didWorkersFinished):
            self.waitCloseAutoSaveWorkerLoop.stop()
        
    def abortSavingInitialisation(self):
        self.titleLabel.setText(
            'Saving data process cancelled.', color=self.titleColor
        )
        self.closeGUI = False
    
    def askSaveOnClosing(self, event):
        if self.saveAction.isEnabled() and self.titleLabel.text != 'Saved!':
            msg = widgets.myMessageBox()
            txt = html_utils.paragraph('Do you want to <b>save before closing?</b>')
            _, noButton, yesButton = msg.question(
                self, 'Save?', txt,
                buttonsTexts=('Cancel', 'No', 'Yes')
            )
            if msg.cancel:
                event.ignore()
                return False
            
            if msg.clickedButton == yesButton:
                self.closeGUI = True
                QTimer.singleShot(100, self.saveAction.trigger)
                event.ignore()
                return False
        return True
    
    def clearMemory(self):
        if not hasattr(self, 'data'):
            return
        self.logger.info('Clearing memory...')
        for posData in self.data:
            try:
                del posData.img_data
            except Exception as e:
                pass
            try:
                del posData.segm_data
            except Exception as e:
                pass
            try:
                del posData.ol_data_dict
            except Exception as e:
                pass
            try:
                del posData.fluo_data_dict
            except Exception as e:
                pass
            try:
                del posData.ol_data
            except Exception as e:
                pass
        del self.data

    def onEscape(self):
        self.setUncheckedAllButtons()
        self.setUncheckedAllCustomAnnotButtons()
        if hasattr(self, 'tempLayerImg1'):
            self.tempLayerImg1.setImage(self.emptyLab)
        self.isMouseDragImg1 = False
        self.typingEditID = False
        if self.highlightedID != 0:
            self.highlightedID = 0
            self.guiTabControl.highlightCheckbox.setChecked(False)
            self.highlightIDcheckBoxToggled(False)
            # self.updateAllImages()
        try:
            self.polyLineRoi.clearPoints()
        except Exception as e:
            pass
    
    def closeEvent(self, event):
        self.onEscape()
        self.saveWindowGeometry()

        if self.slideshowWin is not None:
            self.slideshowWin.close()
        if self.ccaTableWin is not None:
            self.ccaTableWin.close()
        
        proceed = self.askSaveOnClosing(event)
        if not proceed:
            return

        self.autoSaveClose()
        
        if self.autoSaveActiveWorkers:
            progressWin = apps.QDialogWorkerProgress(
                title='Closing autosaving worker', parent=self,
                pbarDesc='Closing autosaving worker...'
            )
            progressWin.show(self.app)
            progressWin.mainPbar.setMaximum(0)
            self.waitCloseAutoSaveWorkerLoop = qutils.QWhileLoop(
                self._waitCloseAutoSaveWorker, period=250
            )
            self.waitCloseAutoSaveWorkerLoop.exec_()
            progressWin.workerFinished = True
            progressWin.close()
        
        # Close the inifinte loop of the thread
        if self.lazyLoader is not None:
            self.lazyLoader.exit = True
            self.lazyLoaderWaitCond.wakeAll()
            self.waitReadH5cond.wakeAll()
        
        if self.storeStateWorker is not None:
            # Close storeStateWorker
            self.storeStateWorker._stop()
            while self.storeStateWorker.isFinished:
                time.sleep(0.05)
        
        # Block main thread while separate threads closes
        time.sleep(0.1)

        self.clearMemory()

        self.logger.info('Closing GUI logger...')
        handlers = self.logger.handlers[:]
        for handler in handlers:
            handler.close()
            self.logger.removeHandler(handler)
        
        # Restore default stdout that was overweritten by setupLogger
        sys.stdout = self.logger.default_stdout
        
        if self.lazyLoader is None:
            self.sigClosed.emit(self)
    
    def storeManualSeparateDrawMode(self, mode):
        self.df_settings.at['manual_separate_draw_mode', 'value'] = mode
        self.df_settings.to_csv(self.settings_csv_path)

    def readSettings(self):
        settings = QSettings('schmollerlab', 'acdc_gui')
        if settings.value('geometry') is not None:
            self.restoreGeometry(settings.value("geometry"))
        # self.restoreState(settings.value("windowState"))

    def saveWindowGeometry(self):
        settings = QSettings('schmollerlab', 'acdc_gui')
        settings.setValue("geometry", self.saveGeometry())
        # settings.setValue("windowState", self.saveState())

    def storeDefaultAndCustomColors(self):
        c = self.overlayButton.palette().button().color().name()
        self.defaultToolBarButtonColor = c
        self.doublePressKeyButtonColor = '#fa693b'

    def showPropsDockWidget(self, checked=False):
        if self.showPropsDockButton.isExpand:
            self.propsDockWidget.setVisible(False)
            self.highlightIDcheckBoxToggled(False)
        else:
            self.highlightedID = self.guiTabControl.propsQGBox.idSB.value()
            if self.isSegm3D:
                self.guiTabControl.propsQGBox.cellVolVox3D_SB.show()
                self.guiTabControl.propsQGBox.cellVolVox3D_SB.label.show()
                self.guiTabControl.propsQGBox.cellVolFl3D_DSB.show()
                self.guiTabControl.propsQGBox.cellVolFl3D_DSB.label.show()
            else:
                self.guiTabControl.propsQGBox.cellVolVox3D_SB.hide()
                self.guiTabControl.propsQGBox.cellVolVox3D_SB.label.hide()
                self.guiTabControl.propsQGBox.cellVolFl3D_DSB.hide()
                self.guiTabControl.propsQGBox.cellVolFl3D_DSB.label.hide()

            self.propsDockWidget.setVisible(True)
            self.propsDockWidget.setEnabled(True)
        self.updateAllImages()
    
    def showEvent(self, event):
        if self.mainWin is not None:
            if not self.mainWin.isMinimized():
                return
            self.mainWin.showAllWindows()
        self.setFocus()
        self.activateWindow()
    
    def super_show(self):
        super().show()

    def show(self):
        self.setFont(_font)
        QMainWindow.show(self)

        self.setWindowState(Qt.WindowNoState)
        self.setWindowState(Qt.WindowActive)
        self.raise_()

        self.readSettings()
        self.storeDefaultAndCustomColors()

        self.h = self.navSpinBox.size().height()
        fontSizeFactor = None
        heightFactor = None
        if 'bottom_sliders_zoom_perc' in self.df_settings.index:
            val = int(self.df_settings.at['bottom_sliders_zoom_perc', 'value'])
            if val != 100:
                fontSizeFactor = val/100
                heightFactor = val/100

        self.defaultWidgetHeightBottomLayout = self.h
        self.checkBoxesHeight = 14
        self.fontPixelSize = 11
        self.defaultBottomLayoutHeight = self.img1BottomGroupbox.height()
        
        self.bottomLayout.setStretch(0, 0)
        self.bottomLayout.addSpacing(self.quickSettingsGroupbox.width())
        self.resizeSlidersArea(
            fontSizeFactor=fontSizeFactor, heightFactor=heightFactor
        )
        self.bottomScrollArea.hide()

        self.gui_initImg1BottomWidgets()
        self.img1BottomGroupbox.hide()

        w = self.showPropsDockButton.width()
        h = self.showPropsDockButton.height()

        self.showPropsDockButton.setMaximumWidth(15)
        self.showPropsDockButton.setMaximumHeight(60)

        self.graphLayout.setFocus()
    
    def resizeSlidersArea(self, fontSizeFactor=None, heightFactor=None):
        global _font
        if heightFactor is None:
            self.newCheckBoxesHeight = self.checkBoxesHeight
            self.newHeight = self.h
        else:
            self.newHeight = round(self.h*heightFactor)
            self.newCheckBoxesHeight = round(self.checkBoxesHeight*heightFactor)
        
        if fontSizeFactor is None:
            newFontSize = self.fontPixelSize
        else:
            newFontSize = round(self.fontPixelSize*fontSizeFactor)
        newFont = QFont()
        newFont.setPixelSize(newFontSize)
        _font = newFont
        self.zProjComboBox.setFont(newFont)
        self.t_label.setFont(newFont)
        self.zProjOverlay_CB.setFont(newFont)
        self.annotateRightHowCombobox.setFont(newFont)
        self.drawIDsContComboBox.setFont(newFont)
        self.showTreeInfoCheckbox.setFont(newFont)
        self.highlightZneighObjCheckbox.setFont(newFont)
        self.navSpinBox.setFont(newFont)
        self.zSliceSpinbox.setFont(newFont)
        self.SizeZlabel.setFont(newFont)
        self.navSizeLabel.setFont(newFont)
        self.overlay_z_label.setFont(newFont)
        self.img1BottomGroupbox.setFont(newFont)
        self.rightBottomGroupbox.setFont(newFont)
        try:
            self.img1.alphaScrollbar.label.setFont(newFont)
        except Exception as e:
            pass
        for i in range(self.annotOptionsLayout.count()):
            widget = self.annotOptionsLayout.itemAt(i).widget()
            widget.setFont(newFont)
        for i in range(self.annotOptionsLayoutRight.count()):
            widget = self.annotOptionsLayoutRight.itemAt(i).widget()
            widget.setFont(newFont)
        try:
            for channel, items in self.overlayLayersItems.items():
                alphaScrollbar = items[2]
                alphaScrollbar.label.setFont(newFont)
        except:
            pass
        QTimer.singleShot(100, self._resizeSlidersArea)
    
    def _resizeSlidersArea(self):
        self.navigateScrollBar.setFixedHeight(self.newHeight)
        self.zSliceScrollBar.setFixedHeight(self.newHeight)
        self.zSliceOverlay_SB.setFixedHeight(self.newHeight)
        self.zProjComboBox.setFixedHeight(self.newHeight)
        self.zProjOverlay_CB.setFixedHeight(self.newHeight)
        self.navSpinBox.setFixedHeight(self.newHeight)
        self.zSliceSpinbox.setFixedHeight(self.newHeight)
        try:
            self.img1.alphaScrollbar.setFixedHeight(self.newHeight)
        except Exception as e:
            pass
        try:
            for channel, items in self.overlayLayersItems.items():
                alphaScrollbar = items[2]
                alphaScrollbar.setFixedHeight(self.newHeight)
        except:
            pass
        checkBoxStyleSheet = (
            'QCheckBox::indicator {'
                f'width: {self.newCheckBoxesHeight}px;'
                f'height: {self.newCheckBoxesHeight}px'
            '}'
        )
        for i in range(self.annotOptionsLayout.count()):
            widget = self.annotOptionsLayout.itemAt(i).widget()
            if isinstance(widget, QCheckBox):
                widget.setStyleSheet(checkBoxStyleSheet)
        for i in range(self.annotOptionsLayoutRight.count()):
            widget = self.annotOptionsLayoutRight.itemAt(i).widget()
            if isinstance(widget, QCheckBox):
                widget.setStyleSheet(checkBoxStyleSheet)
        self.zSliceCheckbox.setStyleSheet(checkBoxStyleSheet)

    def resizeEvent(self, event):
        if hasattr(self, 'ax1'):
            self.ax1.autoRange()
