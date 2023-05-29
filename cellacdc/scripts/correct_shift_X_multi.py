import os

import argparse

import re

import skimage.io

import numpy as np

from acdctools.plot import imshow

#import threading

import concurrent.futures

import copy

import json
#Change this if your data structure is different:#
def finding_base_tif_files_path(root_path):
    base_tif_files_paths =[]
    tif_files_paths = []
    folder_list = os.listdir(root_path)
    folder_list = [os.path.join(root_path, folder_name, 'Images') for folder_name in folder_list if folder_name.lower().startswith(FOLDER_FILTER)]
    for folder_name in folder_list:
        folder_cont = os.listdir(folder_name)
        for file_name in folder_cont:
            if re.match(INCLUDE_PATTERN_TIF_BASESEARCH, file_name):
                base_tif_files_paths.append(os.path.join(folder_name, file_name))
                tif_files_paths.append(folder_name)
    return base_tif_files_paths, tif_files_paths
##################################################

with open('config.json', 'r') as input_file:
    config_raw = input_file.read()
for line in config_raw.splitlines():
    if re.search('x_mult_INCLUDE_PATTERN_TIF_SEARCH'):
        line = line.split(':', 1)[1].strip().lstrip().rstrip(',')
        INCLUDE_PATTERN_TIF_SEARCH = line
    if re.search('x_mult_INCLUDE_PATTERN_TIF_BASESEARCH'):
        line = line.split(':', 1)[1].strip().lstrip().rstrip(',')
        INCLUDE_PATTERN_TIF_BASESEARCH = line
config = json.load(config_raw)
PREVIEW_Z_STACK = config['correct_shift_x_multi']['PREVIEW_Z_STACK']
PREVIEW_Z = config['correct_shift_x_multi']['PREVIEW_Z']
NEW_PATH_SUF = config['correct_shift_x_multi']['NEW_PATH_SUF']
#INCLUDE_PATTERN_TIF_SEARCH = config['correct_shift_x_multi']['INCLUDE_PATTERN_TIF_SEARCH']
#INCLUDE_PATTERN_TIF_BASESEARCH = config['correct_shift_x_multi']['INCLUDE_PATTERN_TIF_BASESEARCH']
FOLDER_FILTER = config['correct_shift_x_multi']['FOLDER_FILTER']

def correct_constant_shift_X_img(img, shift):
    for i, row in enumerate(img[::2]):
        l = i*2
        img[l] = np.roll(row, shift)
    return img

def correct_constant_shift_X(z_stack, shift):
    for z, img in enumerate(z_stack): 
        for i, row in enumerate(img[::2]):
            l = i*2
            z_stack[z, l] = np.roll(row, shift)
    return z_stack

def find_other_tif(file_path):
    folder_path = os.path.dirname(file_path)
    file_list = os.listdir(folder_path)
    file_list = [filename for filename in file_list if filename.lower().endswith('.tif')]
    return file_list

def finding_shift(tif_data, shift):
    eval_img = (tif_data[PREVIEW_Z_STACK][PREVIEW_Z]).copy()
    eval_img = correct_constant_shift_X_img(eval_img, shift)
    imshow(tif_data[PREVIEW_Z_STACK][PREVIEW_Z], eval_img)
    while True:
        answer = input('Do you want to proceed with the shift or change it?([y]/n/"number"/help)')
        if answer.lower() == 'n':
            exit()
        elif answer.isdigit():
            shift = int(answer)
            shift = finding_shift(tif_data, shift)
            return shift
        elif answer.lstrip('-').isdigit():
            shift = int(answer)
            shift = finding_shift(tif_data, shift)
            return shift
        elif answer.lower() == 'help':
            print('Change the shown image by changing PREVIEW_Z_STACK and PREVIEW_Z in the beginning of the code. \nChange the ending of the new file name by changing NEW_PATH_SUF in the code. \nCurrent z stack and z displayed: ' + str(PREVIEW_Z_STACK) + ' ' +str(PREVIEW_Z) + '\nCurrent ending: ' + NEW_PATH_SUF)
            finding_shift(tif_data, shift)
            return shift
        elif not answer:
            return shift
        elif answer.lower() == 'y':
            return shift
        else:
            print('The input is not an integer')

def shiftingstuff_main(shift, tif_data, tif_path):
    corrected_data = tif_data.copy()
    for frame_i, img in enumerate(tif_data):
        corrected_data[frame_i] = correct_constant_shift_X(img.copy(), shift)
    new_path = tif_path.replace('.tif', NEW_PATH_SUF + '.tif')
    skimage.io.imsave(new_path, corrected_data, check_contrast=False)
    print("Saved under:\n" + str(new_path))
    return

def shiftingstuff_other(shifttif):
    tif_data = skimage.io.imread(shifttif[1])
    shiftingstuff_main(shifttif[0], tif_data, shifttif[1])
    del tif_data
    return

def sequential():
    parser = argparse.ArgumentParser()
    parser.add_argument('root_path', help='Path to the folder containing all the folders with the positions')
    args = parser.parse_args()
    root_path = args.root_path

    base_file_paths, other_files_paths = finding_base_tif_files_path(root_path)
    print('Path: \n' + root_path)
    print('Base files found:\n' + "\n".join(base_file_paths))
    if base_file_paths == []:
        print('No files found!')
        exit()
    while True:
        answer = input('Do you want to shift the other .tif files in the folders too? ([y]/n/help)')
        if answer.lower() == 'n':
            scan_other = False
            break
        elif answer.lower() == 'help':
            print('You can change the regex pattern in the beginning of the code (EXCLUDE_PATTERN_TIF_SEARCH). \nIf you dont know regex, ask Chat_GPT to generate one for you by giving it examples of file names and then asking it to generate a regex code which excludes the files you want to exclude. \nCurrent expression is: ' + INCLUDE_PATTERN_TIF_SEARCH)
            exit()
        else:
            scan_other = True
            break

    tif_files_master = []
    for i, tif_path in enumerate(base_file_paths):
        shift = 0
        tif_data = skimage.io.imread(tif_path)
        print('You are looking at:\n' + str(tif_path) + '\nPlease close the window after inspecting if the shift value is right in order to proceed.')
        shift = finding_shift(tif_data, shift)
        tif_files_master.append([shift, tif_path])
        del tif_data
        if scan_other == True:
            other_tif_files = []
            other_tif_files = find_other_tif(tif_path)    
            other_tif_files = [tif_file for tif_file in other_tif_files if re.match(INCLUDE_PATTERN_TIF_SEARCH, tif_file)]
            other_tif_files = [os.path.join(other_files_paths[i], tif_file) for tif_file in other_tif_files]
            for other_tif_file in other_tif_files:
                tif_files_master.append([shift, other_tif_file])
    return tif_files_master

if __name__ == "__main__":
    tif_files_master = sequential()
    print('\nFiles with shift:\n')
    for sub_list in tif_files_master:
        print('Shift: ' + str(sub_list[0]) + '\nPath:' + str(sub_list[1]) + '\n')
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = []
        futures = [executor.submit(shiftingstuff_other, shifttif) for shifttif in tif_files_master]
        results = [future.result() for future in futures]
    print('Done!')
    exit()