import os

import numpy as np

import skimage.exposure
import skimage.filters

from .unet import model
from .unet import neural_network
from .unet import segment

from tensorflow import keras

from tqdm import tqdm

import apps

class progressCallback(keras.callbacks.Callback):
    def __init__(self, signals):
        self.signals = signals

    def on_predict_begin(self, logs=None):
        pass

    def on_predict_batch_begin(self, batch, logs=None):
        pass

    def on_predict_batch_end(self, batch, logs=None):
        innerPbar_available = self.signals[1]
        if innerPbar_available:
            self.signals[0].innerProgressBar.emit(1)
        else:
            self.signals[0].progressBar.emit(1)

class Model:
    def __init__(self, is_phase_contrast=True):
        # Initialize model
        self.model = model.unet(
            pretrained_weights=None,
            input_size=(None,None,1)
        )

        # Get the path where the weights are saved.
        # We suggest saving the weights files into a 'model' subfolder
        script_path = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.join(script_path, 'model')

        if is_phase_contrast:
            weights_fn = 'unet_weights_batchsize_25_Nepochs_100_SJR0_10.hdf5'
        else:
            weights_fn = 'unet_weights_BF_batchsize_25_Nepochs_100_SJR_0_1.hdf5'

        weights_path = os.path.join(model_path, weights_fn)

        if not os.path.exists(model_path):
            raise FileNotFoundError(f'Weights file not found in {model_path}')

        self.model.load_weights(weights_path)

    def yeaz_preprocess(self, image, tqdm_pbar=None):
        image = skimage.filters.gaussian(image, sigma=1)
        # image = skimage.exposure.equalize_adapthist(image)
        image = image/image.max()
        image = skimage.exposure.equalize_adapthist(image)
        if tqdm_pbar is not None:
            tqdm_pbar.emit(1)
        return image

    def segment(self, image, thresh_val=0.0, min_distance=10):
        # Preprocess image
        image = self.yeaz_preprocess(image)

        if thresh_val == 0:
            thresh_val = None

        # pad with zeros such that is divisible by 16
        (nrow, ncol) = image.shape
        row_add = 16-nrow%16
        col_add = 16-ncol%16
        pad_info = ((0, row_add), (0, col_add))
        padded = np.pad(image, pad_info, 'constant')
        x = padded[np.newaxis,:,:,np.newaxis]

        prediction = self.model.predict(x, batch_size=1, verbose=1)[0,:,:,0]

        # remove padding with 0s
        prediction = prediction[0:-row_add, 0:-col_add]

        # Label the cells
        thresh = neural_network.threshold(prediction, thresh_val=thresh_val)
        lab = segment.segment(thresh, prediction, min_distance=min_distance)
        return lab.astype(np.uint16)

    def segment3DT(self, timelapse3D, thresh_val=0.0, min_distance=10, signals=None):
        signals[0].progress.emit(f'Preprocessing images...')
        signals[0].create_tqdm.emit(len(timelapse3D))
        timelapse3D = np.array([
            self.yeaz_preprocess(image, tqdm_pbar=signals[0].progress_tqdm)
            for image in timelapse3D
        ])
        signals[0].signal_close_tqdm.emit()

        if thresh_val == 0:
            thresh_val = None

        # pad with zeros such that is divisible by 16
        (nrow, ncol) = timelapse3D[0].shape
        row_add = 16-nrow%16
        col_add = 16-ncol%16
        pad_info = ((0, 0), (0, row_add), (0, col_add))
        padded = np.pad(timelapse3D, pad_info, 'constant')

        x = padded[:, :, :, np.newaxis]

        signals[0].progress.emit(f'Predicting (the future) with YeaZ...')

        prediction = self.model.predict(
            x, batch_size=1, verbose=1, callbacks=[progressCallback(signals)]
        )[:,:,:,0]

        signals[0].progress.emit(f'Labelling objects with YeaZ...')

        # remove padding with 0s
        prediction = prediction[:, 0:-row_add, 0:-col_add]
        lab_timelapse = np.zeros(prediction.shape, np.uint16)
        signals[0].create_tqdm.emit(len(prediction))
        for t, pred in enumerate(prediction):
            thresh = neural_network.threshold(pred, thresh_val=thresh_val)
            lab = segment.segment(thresh, pred, min_distance=min_distance)
            lab_timelapse[t] = lab.astype(np.uint16)
            signals[0].progress_tqdm.emit(1)
        signals[0].signal_close_tqdm.emit()
        return lab_timelapse
