import sys
import re
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle, Circle, PathPatch, Path
import numpy as np
import tkinter as tk
import cv2
import traceback
from collections import OrderedDict
from MyWidgets import Slider, Button, MyRadioButtons
from skimage.measure import label, regionprops
import skimage.measure
import skimage.morphology
import skimage.exposure
import skimage.draw
import skimage.registration
import skimage.color
from matplotlib.ticker import FormatStrFormatter
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)
from pyglet.canvas import Display
from skimage.color import gray2rgba, label2rgb
from skimage.exposure import equalize_adapthist
from skimage import img_as_float
from skimage.filters import (
    threshold_otsu, threshold_yen, threshold_isodata,
    threshold_li, threshold_mean, threshold_triangle,
    threshold_minimum
)
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import math
from time import time
from lib import text_label_centroid
import matplotlib.ticker as ticker

import pyqtgraph as pg
from pyqtgraph.Qt import QtGui
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtWidgets import (
    QAction, QApplication, QMainWindow, QMenu, QLabel, QToolBar,
    QScrollBar, QWidget, QVBoxLayout, QLineEdit, QPushButton,
    QHBoxLayout, QDialog, QFormLayout
)

import qrc_resources

class my_paint_app:
    def __init__(self, label_img, ID, rp, eps_percent=0.01, del_small_obj=False,
                 overlay_img=None):
        # matplolib dark mode
        plt.style.use('dark_background')
        plt.rc('axes', edgecolor='0.1')

        """Initialize attributes"""
        self.cancel = False
        self.ID_bud = label_img.max() + 1
        self.ID_moth = ID
        self.label_img = label_img
        self.coords_delete = []
        self.overlay_img = skimage.exposure.equalize_adapthist(overlay_img)
        self.num_cells = 1
        """Build image containing only selected ID obj"""
        only_ID_img = np.zeros_like(label_img)
        only_ID_img[label_img == ID] = ID
        all_IDs = [obj.label for obj in rp]
        obj_rp = rp[all_IDs.index(ID)]
        min_row, min_col, max_row, max_col = obj_rp.bbox
        obj_bbox_h = max_row - min_row
        obj_bbox_w = max_col - min_col
        side_len = max([obj_bbox_h, obj_bbox_w])
        obj_bbox_cy = min_row + obj_bbox_h/2
        obj_bbox_cx = min_col + obj_bbox_w/2
        obj_bottom = int(obj_bbox_cy - side_len/2)
        obj_left = int(obj_bbox_cx - side_len/2)
        obj_top = obj_bottom + side_len
        obj_right = obj_left + side_len
        self.bw = 10
        self.xlims = (obj_left-self.bw, obj_right+self.bw)
        self.ylims = (obj_top+self.bw, obj_bottom-self.bw)
        self.only_ID_img = only_ID_img
        self.sep_bud_label = only_ID_img.copy()
        self.eraser_mask = np.zeros(self.label_img.shape, bool)
        self.small_obj_mask = np.zeros(only_ID_img.shape, bool)

        """generate image plot and connect to events"""
        self.fig = plt.Figure()
        self.ax = self.fig.add_subplot()
        self.fig.subplots_adjust(bottom=0.25)
        (self.ax).imshow(self.only_ID_img)
        (self.ax).set_xlim(*self.xlims)
        (self.ax).set_ylim(self.ylims)
        (self.ax).axis('off')
        (self.fig).suptitle('Draw a curve with the right button to separate cell.\n'
                            'Delete object with mouse wheel button\n'
                            'Erase with mouse left button', y=0.95)

        """Find convexity defects"""
        try:
            cnt, defects = self.convexity_defects(
                                              self.only_ID_img.astype(np.uint8),
                                              eps_percent)
        except:
            defects = None
        if defects is not None:
            defects_points = [0]*len(defects)
            for i, defect in enumerate(defects):
                s,e,f,d = defect[0]
                x,y = tuple(cnt[f][0])
                defects_points[i] = (y,x)
                self.ax.plot(x,y,'r.')

        """Embed plt window into a tkinter window"""
        sub_win = embed_tk('Mother-bud zoom', [1024,768,400,150], self.fig)


        """Create buttons"""
        self.ax_ok_B = self.fig.add_subplot(position=[0.2, 0.2, 0.1, 0.03])
        self.ax_overlay_B = self.fig.add_subplot(position=[0.8, 0.2, 0.1, 0.03])
        self.alpha_overlay_sl_ax = self.fig.add_subplot(
                                                 position=[0.7, 0.2, 0.1, 0.03])
        self.brightness_overlay_sl_ax = self.fig.add_subplot(
                                                 position=[0.6, 0.2, 0.1, 0.03])
        self.ok_B = Button(self.ax_ok_B, 'Happy\nwith that', canvas=sub_win.canvas,
                            color='0.1', hovercolor='0.25', presscolor='0.35')
        self.overlay_B = Button(self.ax_overlay_B, 'Overlay',
                            canvas=sub_win.canvas,
                            color='0.1', hovercolor='0.25', presscolor='0.35')
        self.alpha_overlay_sl = Slider(self.alpha_overlay_sl_ax,
                           'alpha', -0.1, 1.1,
                            canvas=sub_win.canvas,
                            valinit=0.3,
                            valstep=0.01,
                            color='0.2',
                            init_val_line_color='0.25',
                            valfmt='%1.2f',
                            orientation='vertical')
        self.brightness_overlay_sl = Slider(self.brightness_overlay_sl_ax,
                           'brightness', 0, 2,
                            canvas=sub_win.canvas,
                            valinit=1,
                            valstep=0.01,
                            color='0.2',
                            init_val_line_color='0.25',
                            valfmt='%1.2f',
                            orientation='vertical')
        """Connect to events"""
        (sub_win.canvas).mpl_connect('button_press_event', self.mouse_down)
        (sub_win.canvas).mpl_connect('button_release_event', self.mouse_up)
        self.cid_brush_circle = (sub_win.canvas).mpl_connect(
                                                    'motion_notify_event',
                                                    self.draw_brush_circle)
        (sub_win.canvas).mpl_connect('key_press_event', self.key_down)
        (sub_win.canvas).mpl_connect('resize_event', self.resize)
        (sub_win.root).protocol("WM_DELETE_WINDOW", self.abort_exec)
        self.overlay_B.on_clicked(self.toggle_overlay)
        self.ok_B.on_clicked(self.ok)
        self.alpha_overlay_sl.on_changed(self.update_img)
        self.brightness_overlay_sl.on_changed(self.update_img)
        self.sub_win = sub_win
        self.clicks_count = 0
        self.brush_size = 2
        self.eraser_on = True
        self.overlay_on = False
        self.set_labRGB_colors()
        sub_win.root.wm_attributes('-topmost',True)
        sub_win.root.focus_force()
        sub_win.root.after_idle(sub_win.root.attributes,'-topmost',False)
        sub_win.root.mainloop()

    def toggle_overlay(self, event):
        self.overlay_on = not self.overlay_on
        if self.overlay_on:
            self.alpha_overlay_sl_ax.set_visible(True)
            self.brightness_overlay_sl_ax.set_visible(True)
        else:
            self.alpha_overlay_sl_ax.set_visible(False)
            self.brightness_overlay_sl_ax.set_visible(False)
        self.update_img(None)

    def set_labRGB_colors(self):
        # Generate a colormap as sparse as possible given the max ID.
        gradient = np.linspace(255, 0, self.num_cells, dtype=int)
        labelRGB_colors = np.asarray([plt.cm.viridis(i) for i in gradient])
        self.labRGB_colors = labelRGB_colors

    def key_down(self, event):
        key = event.key
        if key == 'enter':
            self.ok(None)
        elif key == 'ctrl+z':
            self.undo(None)
        elif key == 'up':
            self.brush_size += 1
            self.draw_brush_circle(event)
        elif key == 'down':
            self.brush_size -= 1
            self.draw_brush_circle(event)
        elif key == 'x':
            # Switch eraser mode on or off
            self.eraser_on = not self.eraser_on
            self.draw_brush_circle(event)

    def resize(self, event):
        # [left, bottom, width, height]
        (self.ax_left, self.ax_bottom,
        self.ax_right, self.ax_top) = self.ax.get_position().get_points().flatten()
        B_h = 0.08
        B_w = 0.1
        self.ax_ok_B.set_position([self.ax_right-B_w, self.ax_bottom-B_h-0.01,
                                   B_w, B_h])
        self.ax_overlay_B.set_position([self.ax_left, self.ax_bottom-B_h-0.01,
                                   B_w*2, B_h])
        self.alpha_overlay_sl_ax.set_position([self.ax_right+0.05,
                                               self.ax_bottom,
                                               B_w/3,
                                               self.ax_top-self.ax_bottom])
        self.brightness_overlay_sl_ax.set_position([
                                               self.ax_right+0.05+B_w/3+0.05,
                                               self.ax_bottom,
                                               B_w/3,
                                               self.ax_top-self.ax_bottom])
        if self.overlay_img is None:
            self.ax_overlay_B.set_visible(False)
        self.alpha_overlay_sl_ax.set_visible(False)
        self.brightness_overlay_sl_ax.set_visible(False)

    def update_img(self, event):
        lab = self.sep_bud_label.copy()
        for y, x in self.coords_delete:
            del_ID = self.sep_bud_label[y, x]
            lab[lab == del_ID] = 0
        rp = skimage.measure.regionprops(lab)
        num_cells = len(rp)
        if self.num_cells != num_cells:
            self.set_labRGB_colors()
        if not self.overlay_on:
            img = lab
        else:
            brightness = self.brightness_overlay_sl.val
            img = skimage.color.label2rgb(
                                lab,image=self.overlay_img*brightness,
                                bg_label=0,
                                bg_color=(0.1,0.1,0.1),
                                colors=self.labRGB_colors,
                                alpha=self.alpha_overlay_sl.val
                                )
            img = np.clip(img, 0, 1)
        self.ax.clear()
        self.ax.imshow(img)
        self.ax.set_xlim(*self.xlims)
        self.ax.set_ylim(*self.ylims)
        self.ax.axis('off')
        for t in self.ax.texts:
            t.set_visible(False)
        for obj in rp:
            y, x = obj.centroid
            txt = f'{obj.label}'
            self.ax.text(
                    int(x), int(y), txt, fontsize=18,
                    fontweight='semibold', horizontalalignment='center',
                    verticalalignment='center', color='k', alpha=1)
        (self.sub_win.canvas).draw_idle()

    def mouse_down(self, event):
        if event.inaxes == self.ax and event.button == 3:
            x = int(event.xdata)
            y = int(event.ydata)
            if self.clicks_count == 0:
                self.x0 = x
                self.y0 = y
                self.cid_line = (self.sub_win.canvas).mpl_connect(
                                                         'motion_notify_event',
                                                                self.draw_line)
                self.pltLine = Line2D([self.x0, self.x0], [self.y0, self.y0])
                self.clicks_count = 1
            elif self.clicks_count == 1:
                self.x1 = x
                self.y1 = y
                (self.sub_win.canvas).mpl_disconnect(self.cid_line)
                self.cid_bezier = (self.sub_win.canvas).mpl_connect(
                                                         'motion_notify_event',
                                                              self.draw_bezier)
                self.clicks_count = 2
            elif self.clicks_count == 2:
                self.x2 = x
                self.y2 = y
                (self.sub_win.canvas).mpl_disconnect(self.cid_bezier)
                self.separate_cb()
                self.clicks_count = 0

        elif event.inaxes == self.ax and event.button == 2:
            xp = int(event.xdata)
            yp = int(event.ydata)
            self.coords_delete.append((yp, xp))
            self.update_img(None)

        elif event.inaxes == self.ax and event.button == 1:
            (self.sub_win.canvas).mpl_disconnect(self.cid_brush_circle)
            self.xb, self.yb = self.ax_transData_and_coerce(self.ax, event.x,
                                                                     event.y,
                                                        self.label_img.shape)
            self.apply_brush(event)
            self.cid_brush = (self.sub_win.canvas).mpl_connect(
                                                     'motion_notify_event',
                                                          self.apply_brush)

    def get_poly_brush(self, yxc1, yxc2, r):
        # see https://en.wikipedia.org/wiki/Tangent_lines_to_circles
        R = r
        y1, x1 = yxc1
        y2, x2 = yxc2
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
                                                    [x3, x4, x6, x5])
        else:
            rr_poly, cc_poly = [], []
        return rr_poly, cc_poly

    def apply_brush(self, event):
        if event.button == 1:
            x, y = self.ax_transData_and_coerce(self.ax, event.x, event.y,
                                                        self.label_img.shape)

            rr, cc = skimage.draw.disk((y, x), radius=self.brush_size,
                                               shape=self.label_img.shape)
            rr_poly, cc_poly = self.get_poly_brush((self.yb, self.xb), (y, x),
                                                    self.brush_size)
            self.xb, self.yb = x, y
            if self.eraser_on:
                self.eraser_mask[rr, cc] = True
                self.eraser_mask[rr_poly, cc_poly] = True
                self.sep_bud_label[self.eraser_mask] = 0
            else:
                self.sep_bud_label[rr, cc] = self.ID_moth
                self.sep_bud_label[rr_poly, cc_poly] = self.ID_moth
                self.eraser_mask[rr, cc] = False
                self.eraser_mask[rr_poly, cc_poly] = False
            self.update_img(None)
            c = 'r' if self.eraser_on else 'g'
            self.brush_circle = matplotlib.patches.Circle((x, y),
                                    radius=self.brush_size,
                                    fill=False,
                                    color=c, lw=2)
            (self.ax).add_patch(self.brush_circle)
            (self.sub_win.canvas).draw_idle()


    def draw_line(self, event):
        if event.inaxes == self.ax:
            self.yd = int(event.ydata)
            self.xd = int(event.xdata)
            self.pltLine.set_visible(False)
            self.pltLine = Line2D([self.x0, self.xd], [self.y0, self.yd],
                                   color='r', ls='--')
            self.ax.add_line(self.pltLine)
            (self.sub_win.canvas).draw_idle()

    def draw_bezier(self, event):
        self.xd, self.yd = self.ax_transData_and_coerce(self.ax, event.x,
                                                                 event.y,
                                                    self.label_img.shape)
        try:
            self.plt_bezier.set_visible(False)
        except:
            pass
        p0 = (self.x0, self.y0)
        p1 = (self.xd, self.yd)
        p2 = (self.x1, self.y1)
        self.plt_bezier = PathPatch(
                                 Path([p0, p1, p2],
                                      [Path.MOVETO,
                                       Path.CURVE3,
                                       Path.CURVE3]),
                                     fc="none", transform=self.ax.transData,
                                     color='r')
        self.ax.add_patch(self.plt_bezier)
        (self.sub_win.canvas).draw_idle()

    def ax_transData_and_coerce(self, ax, event_x, event_y, img_shape,
                                return_int=True):
        x, y = ax.transData.inverted().transform((event_x, event_y))
        ymax, xmax = img_shape
        xmin, ymin = 0, 0
        if x < xmin:
            x_coerced = 0
        elif x > xmax:
            x_coerced = xmax-1
        else:
            x_coerced = int(round(x)) if return_int else x
        if y < ymin:
            y_coerced = 0
        elif y > ymax:
            y_coerced = ymax-1
        else:
            y_coerced = int(round(y)) if return_int else y
        return x_coerced, y_coerced



    def nearest_nonzero(self, a, y, x):
        r, c = np.nonzero(a)
        dist = ((r - y)**2 + (c - x)**2)
        min_idx = dist.argmin()
        return a[r[min_idx], c[min_idx]]

    def separate_cb(self):
        c0, r0 = (self.x0, self.y0)
        c1, r1 = (self.x2, self.y2)
        c2, r2 = (self.x1, self.y1)
        rr, cc = skimage.draw.bezier_curve(r0, c0, r1, c1, r2, c2, 1)
        sep_bud_img = np.copy(self.sep_bud_label)
        sep_bud_img[rr, cc] = 0
        self.sep_bud_img = sep_bud_img
        sep_bud_label_0 = skimage.measure.label(self.sep_bud_img, connectivity=1)
        sep_bud_label = skimage.morphology.remove_small_objects(
                                             sep_bud_label_0,
                                             min_size=20,
                                             connectivity=2)
        small_obj_mask = np.logical_xor(sep_bud_label_0>0,
                                        sep_bud_label>0)
        self.small_obj_mask = np.logical_or(small_obj_mask,
                                            self.small_obj_mask)
        rp_sep = skimage.measure.regionprops(sep_bud_label)
        IDs = [obj.label for obj in rp_sep]
        max_ID = self.ID_bud+len(IDs)
        sep_bud_label[sep_bud_label>0] = sep_bud_label[sep_bud_label>0]+max_ID
        rp_sep = skimage.measure.regionprops(sep_bud_label)
        IDs = [obj.label for obj in rp_sep]
        areas = [obj.area for obj in rp_sep]
        curr_ID_bud = IDs[areas.index(min(areas))]
        curr_ID_moth = IDs[areas.index(max(areas))]
        sep_bud_label[sep_bud_label==curr_ID_moth] = self.ID_moth
        # sep_bud_label = np.zeros_like(sep_bud_label)
        sep_bud_label[sep_bud_label==curr_ID_bud] = self.ID_bud+len(IDs)-2
        temp_sep_bud_lab = sep_bud_label.copy()
        self.rr = []
        self.cc = []
        self.val = []
        for r, c in zip(rr, cc):
            if self.only_ID_img[r, c] != 0:
                ID = self.nearest_nonzero(sep_bud_label, r, c)
                temp_sep_bud_lab[r,c] = ID
                self.rr.append(r)
                self.cc.append(c)
                self.val.append(ID)
        self.sep_bud_label = temp_sep_bud_lab
        self.update_img(None)

    def mouse_up(self, event):
        try:
            (self.sub_win.canvas).mpl_disconnect(self.cid_brush)
            self.cid_brush_circle = (self.sub_win.canvas).mpl_connect(
                                                        'motion_notify_event',
                                                        self.draw_brush_circle)
        except:
            pass

    def draw_brush_circle(self, event):
        if event.inaxes == self.ax:
            x, y = self.ax_transData_and_coerce(self.ax, event.x, event.y,
                                                        self.label_img.shape)
            try:
                self.brush_circle.set_visible(False)
            except:
                pass
            c = 'r' if self.eraser_on else 'g'
            self.brush_circle = matplotlib.patches.Circle((x, y),
                                    radius=self.brush_size,
                                    fill=False,
                                    color=c, lw=2)
            self.ax.add_patch(self.brush_circle)
            (self.sub_win.canvas).draw_idle()

    def convexity_defects(self, img, eps_percent):
        contours, hierarchy = cv2.findContours(img,2,1)
        cnt = contours[0]
        cnt = cv2.approxPolyDP(cnt,eps_percent*cv2.arcLength(cnt,True),True) # see https://www.programcreek.com/python/example/89457/cv22.convexityDefects
        hull = cv2.convexHull(cnt,returnPoints = False) # see https://opencv-python-tutroals.readthedocs.io/en/latest/py_tutorials/py_imgproc/py_contours/py_contours_more_functions/py_contours_more_functions.html
        defects = cv2.convexityDefects(cnt,hull) # see https://opencv-python-tutroals.readthedocs.io/en/latest/py_tutorials/py_imgproc/py_contours/py_contours_more_functions/py_contours_more_functions.html
        return cnt, defects

    def undo(self, event):
        self.coords_delete = []
        sep_bud_img = np.copy(self.only_ID_img)
        self.sep_bud_img = sep_bud_img
        self.sep_bud_label = np.copy(self.only_ID_img)
        self.small_obj_mask = np.zeros(self.only_ID_img.shape, bool)
        self.eraser_mask = np.zeros(self.label_img.shape, bool)
        self.overlay_on = False
        rp = skimage.measure.regionprops(sep_bud_img)
        self.ax.clear()
        self.ax.imshow(self.sep_bud_img)
        (self.ax).set_xlim(*self.xlims)
        (self.ax).set_ylim(*self.ylims)
        text_label_centroid(rp, self.ax, 18, 'semibold', 'center',
                            'center', None, display_ccStage=False,
                            color='k', clear=True)
        self.ax.axis('off')
        (self.sub_win.canvas).draw_idle()

    def ok(self, event):
        # plt.close(self.fig)
        self.sub_win.root.quit()
        self.sub_win.root.destroy()

    def abort_exec(self):
        self.cancel = True
        plt.close(self.fig)
        self.sub_win.root.quit()
        self.sub_win.root.destroy()

class CellsSlideshow_GUI(QMainWindow):
    """Main Window."""

    def __init__(self, parent=None, button_toUncheck=None, Left=50, Top=50):
        self.button_toUncheck = button_toUncheck
        """Initializer."""
        super().__init__(parent)
        self.setWindowTitle("Yeast ACDC - Segm&Track")
        self.setGeometry(Left, Top, 850, 800)

        self.gui_createActions()
        self.gui_createMenuBar()
        self.gui_createToolBars()

        self.gui_connectActions()
        self.gui_createStatusBar()

        self.gui_createGraphics()

        self.gui_connectImgActions()

        self.gui_createImgWidgets()

        mainContainer = QtGui.QWidget()
        self.setCentralWidget(mainContainer)

        mainLayout = QtGui.QGridLayout()
        mainLayout.addWidget(self.graphLayout, 0, 0, 1, 1)
        mainLayout.addLayout(self.img_Widglayout, 1, 0)

        mainContainer.setLayout(mainLayout)

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
        hist.setImageItem(self.img)
        self.graphLayout.addItem(hist, row=1, col=0)

        # Current frame text
        self.frameLabel = pg.LabelItem(justify='center', color='w', size='14pt')
        self.frameLabel.setText(' ')
        self.graphLayout.addItem(self.frameLabel, row=2, col=0, colspan=2)

    def gui_connectImgActions(self):
        self.img.hoverEvent = self.gui_hoverEventImg

    def gui_createImgWidgets(self):
        self.zSlice_scrollBar_img = QScrollBar(Qt.Horizontal)
        self.img_Widglayout = QtGui.QGridLayout()
        self.zSlice_scrollBar_img.setFixedHeight(20)
        self.zSlice_scrollBar_img.setDisabled(True)
        _z_label = QLabel('z-slice  ')
        _font = QtGui.QFont()
        _font.setPointSize(10)
        _z_label.setFont(_font)
        self.img_Widglayout.addWidget(_z_label, 0, 0, alignment=Qt.AlignCenter)
        self.img_Widglayout.addWidget(self.zSlice_scrollBar_img, 0, 1, 2, 20)

        self.img_Widglayout.setContentsMargins(100, 0, 50, 0)

    def gui_hoverEventImg(self, event):
        # Update x, y, value label bottom right
        try:
            x, y = event.pos()
            xdata, ydata = int(round(x)), int(round(y))
            _img = self.img.image
            Y, X = _img.shape
            if xdata >= 0 and xdata < X and ydata >= 0 and ydata < Y:
                val = _img[ydata, xdata]
                self.wcLabel.setText(f'(x={x:.2f}, y={y:.2f}, value={val:.2f})')
            else:
                self.wcLabel.setText(f'')
        except:
            self.wcLabel.setText(f'')

    def loadData(self, frames, frame_i=0):
        self.frames = frames
        self.num_frames = len(frames)
        self.frame_i = frame_i
        self.update_img()

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


    def update_img(self):
        self.frameLabel.setText(
                 f'Current frame = {self.frame_i+1}/{self.num_frames}')
        self.img.setImage(self.frames[self.frame_i])

    def closeEvent(self, event):
        if self.button_toUncheck is not None:
            self.button_toUncheck.setChecked(False)

class YeaZ_ParamsDialog(QDialog):
    def __init__(self):
        self.cancel = True
        super().__init__()
        self.setWindowTitle("YeaZ parameters")

        mainLayout = QVBoxLayout()

        formLayout = QFormLayout()
        formLayout.addRow("Threshold value:", QLineEdit())
        formLayout.addRow("Minimum distance:", QLineEdit())

        threshVal_QLineEdit = formLayout.itemAt(0, 1).widget()
        threshVal_QLineEdit.setText('None')
        threshVal_QLineEdit.setAlignment(Qt.AlignCenter)
        self.threshVal_QLineEdit = threshVal_QLineEdit

        minDist_QLineEdit = formLayout.itemAt(1, 1).widget()
        minDist_QLineEdit.setText('10')
        minDist_QLineEdit.setAlignment(Qt.AlignCenter)
        self.minDist_QLineEdit = minDist_QLineEdit

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
        minDistTxt = self.minDist_QLineEdit.text()
        try:
            self.threshVal = float(threshTxt)
            if self.threshVal > 0 and self.threshVal < 1:
                valid_threshVal = True
            else:
                valid_threshVal = False
        except:
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
        else:
            try:
                self.minDist = int(minDistTxt)
                valid_minDist = True
            except:
                valid_minDist = False
        if not valid_minDist:
            err_msg = (
                'Minimum distance is not valid. Enter an integer'
            )
            msg = QtGui.QMessageBox()
            msg.critical(
                self, 'Invalid minimum distance', err_msg, msg.Ok
            )
            return
        self.close()

    def cancel_cb(self, event):
        self.cancel = True
        self.close()


class editID_QWidget(QDialog):
    def __init__(self, clickedID, IDs):
        self.IDs = IDs
        self.clickedID = clickedID
        self.cancel = True
        self.how = None

        super().__init__()
        self.setWindowTitle("Edit ID")
        mainLayout = QVBoxLayout()

        VBoxLayout = QVBoxLayout()
        msg = QLabel(f'Replace ID {clickedID} with:')
        _font = QtGui.QFont()
        _font.setPointSize(10)
        msg.setFont(_font)
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


class editID_widget:
    def __init__(self, old_ID=None, second_button=False):
        self.old_ID = old_ID
        self.cancel = False
        root = tk.Tk()
        root.lift()
        root.attributes("-topmost", True)
        root.geometry("+800+400")
        self._root = root
        if old_ID is not None:
            label_txt = f'ID = {old_ID} will be replaced\n with new ID'
        else:
            label_txt = 'New ID'
        tk.Label(root, text=label_txt, font=(None, 10)).grid(row=0, columnspan=2)
        ID_strvar = tk.StringVar()
        ID = tk.Entry(root, justify='center', textvariable=ID_strvar)
        ID_strvar.trace_add("write", self._close_brackets)
        ID.grid(row=1, padx=16, pady=4, columnspan=2)
        ID.focus_force()
        if second_button:
            self.ok_for_all_butt = tk.Button(root, command=self._ok_for_all_cb,
                            text='Ok for all next frames',state=tk.DISABLED)
            self.ok_for_all_butt.grid(row=2, pady=4, column=1, padx=4)
            self.ok_butt = tk.Button(root, command=self._quit, text='Ok!',
                                           width=10)
            self.ok_butt.grid(row=2, pady=4, column=0, padx=4)
        else:
            self.ok_butt = tk.Button(root, command=self._quit, text='Ok!',
                                           width=10)
            self.ok_butt.grid(row=2, pady=4, padx=4, columnspan=2)
        tk.Label(root, text='NOTE:\n You can write a list of tuples:\n'
                            '[(old ID, new ID), ...]', font=(None, 10)
                            ).grid(row=3, pady=4, columnspan=2)
        root.bind('<Return>', self._quit)
        root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.entry = ID
        root.mainloop()

    def _close_brackets(self, name=None, index=None, mode=None):
        txt = self.entry.get()
        input_idx = self.entry.index(tk.INSERT)
        input_txt = txt[input_idx-1:input_idx]
        if input_txt == '(':
            self.entry.insert(tk.INSERT, ')')
            self.entry.icursor(self.entry.index(tk.INSERT)-1)
        elif input_txt == '[':
            self.entry.insert(tk.INSERT, ']')
            self.entry.icursor(self.entry.index(tk.INSERT)-1)
        try:
            int(self.entry.get())
            self.ok_for_all_butt['state'] = tk.NORMAL
        except:
            try:
                self.ok_for_all_butt['state'] = tk.DISABLED
            except:
                pass

    def _ok_for_all_cb(self, event=None):
        self.ok_for_all = True
        txt = self.entry.get()
        if txt.find('[') != -1:
            self.new_ID = literal_eval(txt)
        else:
            self.new_ID = int(self.entry.get())
        self._root.quit()
        self._root.destroy()

    def _quit(self, event=None):
        self.ok_for_all = False
        txt = self.entry.get()
        if txt.find('[') != -1:
            self.new_ID = literal_eval(txt)
        else:
            self.new_ID = [(self.old_ID, int(self.entry.get()))]
        self._root.quit()
        self._root.destroy()

    def on_closing(self):
        self.cancel = True
        self._root.quit()
        self._root.destroy()
        # exit('Execution aborted by the user')



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
        except:
            try:
                mng = plt.get_current_fig_manager()
                mng.window.state('zoomed')
            except:
                pass

if __name__ == '__main__':
    # Create the application
    app = QApplication(sys.argv)
    win = YeaZ_ParamsDialog()
    win.show()
    app.setStyle(QtGui.QStyleFactory.create('Fusion'))
    # win.loadData(np.random.randint(0,255, size=(200, 512,512)))
    app.exec_()
    print(win.cancel)
