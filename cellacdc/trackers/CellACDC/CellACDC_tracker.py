import os

from tqdm import tqdm

import numpy as np
from skimage.measure import regionprops
from skimage.segmentation import relabel_sequential

from cellacdc.core import lab_replace_values, np_replace_values


def calc_IoA_matrix(lab, prev_lab, rp, prev_rp, IDs_curr_untracked=None):
    IDs_prev = []
    if IDs_curr_untracked is None:
        IDs_curr_untracked = [obj.label for obj in rp]

    IoA_matrix = np.zeros((len(rp), len(prev_rp)))

    # For each ID in previous frame get IoA with all current IDs
    # Rows: IDs in current frame, columns: IDs in previous frame
    for j, obj_prev in enumerate(prev_rp):
        ID_prev = obj_prev.label
        A_IDprev = obj_prev.area
        IDs_prev.append(ID_prev)
        mask_ID_prev = prev_lab==ID_prev
        intersect_IDs, intersects = np.unique(
            lab[mask_ID_prev], return_counts=True
        )
        for intersect_ID, I in zip(intersect_IDs, intersects):
            if intersect_ID != 0:
                i = IDs_curr_untracked.index(intersect_ID)
                IoA = I/A_IDprev
                IoA_matrix[i, j] = IoA
    return IoA_matrix, IDs_curr_untracked, IDs_prev

def assign(IoA_matrix, IDs_curr_untracked, IDs_prev):
    # Determine max IoA between IDs and assign tracked ID if IoA > 0.4
    max_IoA_col_idx = IoA_matrix.argmax(axis=1)
    unique_col_idx, counts = np.unique(max_IoA_col_idx, return_counts=True)
    counts_dict = dict(zip(unique_col_idx, counts))
    tracked_IDs = []
    old_IDs = []
    for i, j in enumerate(max_IoA_col_idx):
        max_IoU = IoA_matrix[i,j]
        count = counts_dict[j]
        if max_IoU > 0.4:
            tracked_ID = IDs_prev[j]
            if count == 1:
                old_ID = IDs_curr_untracked[i]
            elif count > 1:
                old_ID_idx = IoA_matrix[:,j].argmax()
                old_ID = IDs_curr_untracked[old_ID_idx]
            tracked_IDs.append(tracked_ID)
            old_IDs.append(old_ID)
    return old_IDs, tracked_IDs

def indexAssignment(
        old_IDs, tracked_IDs, IDs_curr_untracked, lab, rp, uniqueID,
        remove_untracked=False, assign_unique_new_IDs=True
    ):
    # Replace untracked IDs with tracked IDs and new IDs with increasing num
    new_untracked_IDs = [ID for ID in IDs_curr_untracked if ID not in old_IDs]
    tracked_lab = lab
    # print('----------------------------')
    # print(f'Assign new IDs uniquely = {assign_unique_new_IDs}')
    # print('***********************')
    if new_untracked_IDs and assign_unique_new_IDs:
        # Relabel new untracked IDs unique IDs
        if remove_untracked:
            new_tracked_IDs = [0]*len(new_untracked_IDs)
        else:
            new_tracked_IDs = [
                uniqueID+i for i in range(len(new_untracked_IDs))
            ]
        lab_replace_values(
            tracked_lab, rp, new_untracked_IDs, new_tracked_IDs
        )
        # print('Current IDs: ', IDs_curr_untracked)
        # print('Previous IDs: ', old_IDs)
        # print('New objects that get a new big ID: ', new_untracked_IDs)
        # print('New unique IDs for the new objects: ', new_tracked_IDs)
    if tracked_IDs:
        lab_replace_values(
            tracked_lab, rp, old_IDs, tracked_IDs, in_place=True
        )
        # print('Old IDs to be tracked: ', old_IDs)
        # print('New IDs replacing old IDs: ', tracked_IDs)
    return tracked_lab

def track_frame(
        prev_lab, prev_rp, lab, rp, IDs_curr_untracked=None,
        uniqueID=None, setBrushID_func=None, posData=None,
        assign_unique_new_IDs=True
    ):
    IoA_matrix, IDs_curr_untracked, IDs_prev = calc_IoA_matrix(
        lab, prev_lab, rp, prev_rp, IDs_curr_untracked=IDs_curr_untracked
    )
    old_IDs, tracked_IDs = assign(
        IoA_matrix, IDs_curr_untracked, IDs_prev
    )

    if posData is None and uniqueID is None:
        uniqueID = max((max(IDs_prev), max(IDs_curr_untracked)))+1
    elif uniqueID is None:
        # Compute starting unique ID
        setBrushID_func(useCurrentLab=False)
        uniqueID = posData.brushID

    tracked_lab = indexAssignment(
        old_IDs, tracked_IDs, IDs_curr_untracked,
        lab.copy(), rp, uniqueID,
        assign_unique_new_IDs=assign_unique_new_IDs
    )
    return tracked_lab

class tracker:
    def __init__(self):
        pass

    def track(self, segm_video, signals=None, export_to: os.PathLike=None):
        tracked_video = np.zeros_like(segm_video)
        for frame_i, lab in enumerate(tqdm(segm_video, ncols=100)):
            if frame_i == 0:
                tracked_video[frame_i] = lab
                continue

            prev_lab = segm_video[frame_i-1]

            prev_rp = regionprops(prev_lab)
            rp = regionprops(lab.copy())

            tracked_lab = track_frame(
                prev_lab, prev_rp, lab, rp
            )

            tracked_video[frame_i] = tracked_lab
            if signals is not None:
                signals.progressBar.emit(1)
        tracked_video = relabel_sequential(tracked_video)[0]
        return tracked_video

    def save_output(self):
        pass
