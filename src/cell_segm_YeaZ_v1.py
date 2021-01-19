import sys
import re
import os
import cv2
from skimage import io
from skimage.exposure import equalize_adapthist
from skimage.measure import label, regionprops
import numpy as np
import pandas as pd
import tkinter as tk
import matplotlib.pyplot as plt
from time import time
from tifffile import TiffFile
from tkinter import messagebox
from segm_FUNCTIONS_v4 import (load_shifts, select_slice_toAlign, align_frames_3D,
                   align_frames_2D, single_entry_messagebox, twobuttonsmessagebox,
                   auto_select_slice, num_frames_toSegm_tk, draw_ROI_2D_frames,
                   text_label_centroid, file_dialog)

script_dirname = os.path.dirname(os.path.realpath(__file__))
unet_path = f'{script_dirname}/YeaZ-unet/unet/'

#append all the paths where the modules are stored. Such that this script
#looks into all of these folders when importing modules.
sys.path.append(unet_path)
import neural_network as nn
from segment import segment

class load_data:
    def __init__(self, path):
        self.path = path
        self.parent_path = os.path.dirname(path)
        self.filename, self.ext = os.path.splitext(os.path.basename(path))
        if self.ext == '.tif' or self.ext == '.png' or self.ext == '.jpg':
            self.tif_path = path
            img_data = io.imread(path)
        elif self.ext == '.npy':
            tif_path = self.substring_path(path, 'phase_contr.tif',
                                           self.parent_path)[0]
            self.tif_path = tif_path
            img_data = np.load(path)
        self.img_data = img_data
        self.info, self.metadata_found = self.metadata(self.tif_path)
        if self.metadata_found:
            try:
                self.SizeT, self.SizeZ = self.data_dimensions(self.info)
            except:
                self.SizeT, self.SizeZ = self.dimensions_entry_widget()
        elif img_data.ndim > 2:
            self.SizeT, self.SizeZ = self.dimensions_entry_widget()
        else:
            self.SizeT, self.SizeZ = 1, 1

    def build_paths(self, filename, parent_path):
        match = re.search('s(\d+)_', filename)
        if match is not None:
            basename = filename[:match.span()[1]-1]
        else:
            basename = single_entry_messagebox(
                     entry_label='Write a common basename for all output files',
                     input_txt=filename,
                     toplevel=False).entry_txt
        base_path = f'{parent_path}/{basename}'
        self.slice_used_align_path = f'{base_path}_slice_used_alignment.csv'
        self.slice_used_segm_path = f'{base_path}_slice_segm.csv'
        self.align_npy_path = f'{base_path}_phc_aligned.npy'
        self.align_shifts_path = f'{base_path}_align_shift.npy'
        self.segm_npy_backup_path = f'{base_path}_segm_YeaZ.npy'
        self.segm_npy_path = f'{base_path}_segm.npy'
        self.pred_npy_path = f'{base_path}_pred.npy'

    def substring_path(self, path, substring, parent_path):
        substring_found = False
        for filename in os.listdir(parent_path):
            if substring == "phase_contr.tif":
                is_match = (filename.find(substring) != -1 or
                            filename.find("phase_contrast.tif") != -1)
            else:
                is_match = filename.find(substring) != -1
            if is_match:
                substring_found = True
                break
        substring_path = f'{parent_path}/{filename}'
        return substring_path, substring_found


    def metadata(self, tif_path):
        try:
            with TiffFile(tif_path) as tif:
                self.metadata = tif.imagej_metadata
            try:
                metadata_found = True
                info = self.metadata['Info']
            except KeyError:
                metadata_found = False
                info = []
        except:
            metadata_found = False
            info = []
        return info, metadata_found

    def data_dimensions(self, info):
        SizeT = int(re.findall('SizeT = (\d+)', info)[0])
        SizeZ = int(re.findall('SizeZ = (\d+)', info)[0])
        return SizeT, SizeZ

    def dimensions_entry_widget(self):
        root = tk.Tk()
        root.geometry("+800+400")
        tk.Label(root,
                 text="Data dimensions not found in metadata.\n"
                      "Provide the following sizes.",
                 font=(None, 12)).grid(row=0, column=0, columnspan=2, pady=4)
        tk.Label(root,
                 text="Number of frames (SizeT)",
                 font=(None, 10)).grid(row=1, pady=4)
        tk.Label(root,
                 text="Number of slices (SizeZ)",
                 font=(None, 10)).grid(row=2, pady=4, padx=8)

        SizeT_entry = tk.Entry(root, justify='center')
        SizeZ_entry = tk.Entry(root, justify='center')

        # Default texts in entry text box
        SizeT_entry.insert(0, '1')
        SizeZ_entry.insert(0, '1')

        SizeT_entry.grid(row=1, column=1, padx=8)
        SizeZ_entry.grid(row=2, column=1, padx=8)

        tk.Button(root,
                  text='OK',
                  command=root.quit,
                  width=10).grid(row=3,
                                 column=0,
                                 pady=16,
                                 columnspan=2)
        SizeT_entry.focus()

        tk.mainloop()

        SizeT = int(SizeT_entry.get())
        SizeZ = int(SizeZ_entry.get())
        root.destroy()
        return SizeT, SizeZ

def find_contours(label_img, cells_ids, group=False, concat=False,
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


plt.dark()
root = tk.Tk()
root.withdraw()

path = file_dialog(title='Select phase contrast or bright-field .tif/.npy file')

print('Loading data...')
data = load_data(path)
data.build_paths(data.filename, data.parent_path)

num_frames = data.SizeT if data.SizeT in list(data.img_data.shape) else 1
num_slices = data.SizeZ if data.SizeZ in list(data.img_data.shape) else 1

print(f'Image data shape = {data.img_data.shape}')
print(f'Number of frames = {num_frames}')
print(f'Number of slices in each z-stack = {num_slices}')

"""Align frames if needed"""
if num_frames > 1 and data.ext == '.tif':
    align = messagebox.askyesno('Align frames?', 'Do you want to align frames?',
                                master=root)
    if align:
        frames = data.img_data
        print('Aligning frames...')
        loaded_shifts, shifts_found = load_shifts(data.parent_path)
        if not shifts_found and num_slices > 1:
            slices = select_slice_toAlign(frames, num_frames,
                                          slice_used_for='alignment').slices
            df_slices = pd.DataFrame({'Slice used for alignment': slices,
                                      'frame_i': range(num_frames)})
            df_slices.set_index('frame_i', inplace=True)
            df_slices.to_csv(data.slice_used_align_path)
        else:
            slices=None
        align_func = align_frames_3D if num_slices>1 else align_frames_2D
        aligned_frames, shifts = align_func(frames, slices=slices,
                                          register=not shifts_found,
                                          user_shifts=loaded_shifts)
        print('Frames aligned!')
        save_align = messagebox.askyesno('Save aligned frames?',
                                         'Do you want to save aligned frames?',
                                         master=root)
        if save_align:
            print('Saving aligned frames...')
            np.save(data.align_npy_path, aligned_frames, allow_pickle=False)
            np.save(data.align_shifts_path, shifts, allow_pickle=False)
            print('Aligned frames saved!')
        frames = aligned_frames
    else:
        frames = data.img_data
else:
    frames = data.img_data

"""Check if segmentation was already performed"""
print('Checking if segmentation was already performed...')
parent_path = os.path.dirname(path)
filenames = os.listdir(parent_path)
last_segm_i = None
for filename in filenames:
    if filename.find('segm.npy') != -1:
        segm_npy_path = f'{parent_path}/{filename}'
        print('Loading segmentation file...')
        segm_npy = np.load(segm_npy_path)
        last_segm_i = len(segm_npy)-1
        segm_npy_found = True
        break
    else:
        segm_npy_found = False

"""Check img data shape and reshape if needed"""
print('Checking img data shape and reshaping if needed...')
ROI_coords = None
if num_slices == 1:
    slices = None
    if num_frames > 1:
        # 2D frames
        pass
    else:
        # 2D snapshot (no alignment required)
        y, x = data.img_data.shape
        frames = np.reshape(data.img_data, (1,y,x))
    ROI_coords = draw_ROI_2D_frames(frames, num_frames,
                        slice_used_for='segmentation and apply ROI if needed',
                        activate_ROI=True).ROI_coords
    if not segm_npy_found:
        segm_npy = np.zeros(frames.shape, int)
elif num_slices > 1:
    if num_frames > 1:
        # 3D frames
        _, z, y, x = frames.shape
        if not segm_npy_found:
            segm_npy = np.zeros((num_frames, y, x), int)
    else:
        # 3D snapshot (no alignment required)
        z, y, x = data.img_data.shape
        frames = np.reshape(data.img_data, (1,z,y,x))
        if not segm_npy_found:
            segm_npy = np.zeros((1, y, x), int)
    if os.path.exists(data.slice_used_align_path):
        df_slices = pd.read_csv(data.slice_used_align_path)
        slices = df_slices['Slice used for alignment'].to_list()
    else:
        slices = [0]
    if os.path.exists(data.slice_used_segm_path):
        df_slices = pd.read_csv(data.slice_used_segm_path)
        slices = df_slices['Slice used for segmentation'].to_list()
        print(df_slices)
    else:
        slices = [0]
    if num_frames == 1:
        select_slice = auto_select_slice(data.img_data, init_slice=0,
                    slice_used_for='segmentation and apply ROI if needed',
                    activate_ROI=True)
        ROI_coords =  select_slice.ROI_coords
        slices = [select_slice.slice]
        df_slices_path = data.slice_used_segm_path
    else:
        print('Loading slice selector GUI...')
        select_slice = select_slice_toAlign(frames, num_frames,
                    init_slice=slices[0],
                    slice_used_for='segmentation and apply ROI if needed',
                    activate_ROI=True,
                    title='Select slices to use for segmentation')
        ROI_coords =  select_slice.ROI_coords
        slices = select_slice.slices
        df_slices_path = data.slice_used_segm_path
    df_slices = pd.DataFrame({'Slice used for segmentation': slices,
                              'frame_i': range(num_frames)})
    df_slices.set_index('frame_i', inplace=True)
    df_slices.to_csv(df_slices_path)

start = 0
if num_frames > 1:
    start, stop = num_frames_toSegm_tk(num_frames, last_segm_i=last_segm_i,
                                                   toplevel=True).frange
    filenames = os.listdir(parent_path)
    for filename in filenames:
        if filename.find('_last_tracked_i.txt') != -1:
            last_tracked_i_path = f'{parent_path}/{filename}'
            with open(last_tracked_i_path, 'w') as txt:
                txt.write(f'{start-1}')
            break


if len(segm_npy) != len(frames) and num_frames > 1:
    # Since there is a mismatch between segm_npy shape and frames shape
    # we pad with zeros
    empty_segm_npy = np.zeros(frames.shape, int)
    empty_segm_npy[:len(segm_npy)] = segm_npy
    segm_npy = empty_segm_npy

save_segm = messagebox.askyesno('Save segmentation?',
                                 'Do you want to save segmentation?',
                                 master=root)

root.destroy()

is_pc = twobuttonsmessagebox('Img mode', 'Select imaging mode',
                             'Phase contrast', 'Bright-field').button_left

if num_frames > 1:
    frames = frames[start:stop]

if ROI_coords is not None:
    y_start, y_end, x_start, x_end = ROI_coords
    if num_slices > 1:
        ROI_img = frames[0, slices[start]][y_start:y_end, x_start:x_end]
    else:
        ROI_img = frames[0][y_start:y_end, x_start:x_end]
    print(f'ROI image data shape = {ROI_img.shape}')

for frame_i, img in enumerate(frames):
    print(f'Segmenting frame {start+frame_i+1}/{num_frames}...')
    if num_slices > 1:
        img = img[slices[start+frame_i]]
    r, c = img.shape
    if ROI_coords is not None:
        y_start, y_end, x_start, x_end = ROI_coords
        img = img[y_start:y_end, x_start:x_end]
    t0 = time()
    img = equalize_adapthist(img)
    img = img*1.0
    pred = nn.prediction(img, is_pc=is_pc)
    # plt.imshow_tk(pred)
    thresh = nn.threshold(pred)
    # plt.imshow_tk(thresh)
    lab = segment(thresh, pred, min_distance=5).astype(int)
    # plt.imshow_tk(lab)
    t_end = time()
    if ROI_coords is not None:
        lab = np.pad(lab, ((y_start, r-y_end), (x_start, c-x_end)))
        img = np.pad(img, ((y_start, r-y_end), (x_start, c-x_end)))
    if num_frames > 1:
        segm_npy[start+frame_i] = lab
    else:
        segm_npy = lab.copy()
    if save_segm:
        np.save(data.segm_npy_path, segm_npy)
        np.save(data.pred_npy_path, pred)

if save_segm:
    np.save(data.segm_npy_backup_path, segm_npy)

rp = regionprops(lab)
IDs = [obj.label for obj in rp]
contours = find_contours(lab, IDs, group=True)


fig, ax = plt.subplots(1,2)

ax[0].imshow(img)
ax[1].imshow(lab)
text_label_centroid(rp, ax[0], 12, 'semibold', 'center', 'center',
                    color='r', clear=True)
text_label_centroid(rp, ax[1], 12, 'semibold', 'center', 'center',
                    clear=True)
for cont in contours:
    x = cont[:,1]
    y = cont[:,0]
    x = np.append(x, x[0])
    y = np.append(y, y[0])
    ax[0].plot(x, y, c='r')

for a in ax:
    a.axis('off')

fig.suptitle(f'Neural network execution time = {t_end-t0: .3f} s', y=0.9,
             size=18)

plt.win_size()
plt.show()
