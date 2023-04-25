import cv2
import random

import numpy as np

import torch

from skimage.measure import regionprops
from skimage.segmentation import relabel_sequential
from skimage.transform import resize

from deepsea.model import DeepSeaTracker
from deepsea.utils import track_cells

from cellacdc import myutils, printl
from cellacdc.models.DeepSea import _init_model, _resize_img
from cellacdc.models.DeepSea import image_size as segm_image_size
from cellacdc.models.DeepSea import _get_segm_transforms

from . import _get_tracker_transforms

SEED = 1234
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
torch.cuda.manual_seed(SEED)
torch.backends.cudnn.deterministic = True

class tracker:
    def __init__(self, gpu=False):
        torch_device, checkpoint, model = _init_model(
            'tracker.pth', DeepSeaTracker, gpu=gpu
        )
        self.torch_device = torch_device
        self._transforms = _get_tracker_transforms()
        self._segm_transforms = _get_segm_transforms()
        self._checkpoint = checkpoint
        self.model = model
    
    def _resize_lab(self, lab, output_shape, rp):
        _lab_obj_to_resize = np.zeros(lab.shape, dtype=np.float16)
        lab_resized = np.zeros(output_shape, dtype=np.uint32)
        for obj in rp:
            _lab_obj_to_resize[obj.slice][obj.image] = 1.0
            _lab_obj_resized = resize(
                _lab_obj_to_resize, output_shape, anti_aliasing=True,
                preserve_range=True
            ).round()
            lab_resized[_lab_obj_resized == 1.0] = obj.label
            _lab_obj_to_resize[:] = 0.0
        return lab_resized

    def _relabel_sequential(self, segm_video):
        relabelled_video = np.zeros_like(segm_video)
        for frame_i, lab in enumerate(segm_video):
            relabelled_lab, _, _ = relabel_sequential(lab)
            relabelled_video[frame_i] = relabelled_lab
        return relabelled_video

    def track(self, segm_video, image, min_size=10, signals=None):
        segm_video = self._relabel_sequential(segm_video)
        labels_list = []
        resize_img_list = []
        for img, lab in zip(image, segm_video):
            img = (255 * ((img - img.min()) / img.ptp())).astype(np.uint8)
            rp = regionprops(lab)
            resized_img = _resize_img(
                img, self.torch_device, self._segm_transforms
            )
            resized_lab = self._resize_lab(
                lab, output_shape=tuple(segm_image_size), rp=rp
            )
            resize_img_list.append(resized_img)
            labels_list.append(resized_lab)
        
        result = track_cells(
            labels_list, resize_img_list, self.model, self.torch_device, 
            transforms=self._transforms, min_size=min_size
        )
        tracked_labels, tracked_centroids, tracked_imgs = result
        tracked_video = self._replace_tracked_IDs(
            labels_list, tracked_labels, tracked_centroids, segm_video
        )

        return tracked_video

    def _replace_tracked_IDs(
            self, resized_labels_list, tracked_labels, tracked_centroids,
            segm_video
        ):
        _zip = zip(tracked_labels, tracked_centroids)
        IDs_prev = []
        tracked_video = np.zeros_like(segm_video)
        for frame_i, track_info_frame in enumerate(_zip):
            tracked_frame_labels, tracked_frame_centroids = track_info_frame
            tracked_frame_IDs = [int(label)+1 for label in tracked_frame_labels]
            lab = resized_labels_list[frame_i]
            tracked_lab = tracked_video[frame_i]
            untracked_lab = segm_video[frame_i]
            rp = regionprops(lab)
            IDs_curr_untracked = [obj.label for obj in rp]
            uniqueID = max(
                max(IDs_prev, default=0), 
                max(IDs_curr_untracked, default=0),
                max(tracked_frame_IDs, default=0)
            ) + 1
            IDs_to_replace = {
                lab[tuple(centr)]:idx
                for idx, centr in enumerate(tracked_frame_centroids)
            }
            IDs_prev = []            
            for obj in rp:
                idx_ID_to_replace = IDs_to_replace.get(obj.label)
                if idx_ID_to_replace is None:
                    newID = uniqueID
                    uniqueID += 1
                else:
                    newID = tracked_frame_IDs[idx_ID_to_replace]
                try:
                    tracked_lab[untracked_lab == obj.label] = newID
                except Exception as e:
                    import pdb; pdb.set_trace()
                IDs_prev.append(newID)
            
            import pdb; pdb.set_trace()
            tracked_video[frame_i] = tracked_lab

        return tracked_video
            
            