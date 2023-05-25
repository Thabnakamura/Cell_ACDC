import os

import argparse

import re

import skimage.io

import numpy as np

from acdctools.plot import imshow

import concurrent.futures

import copy


###############Constants:###################
PREVIEW_Z_STACK = 40
PREVIEW_Z = 14
NEW_PATH_SUF = '' #'' causes old file to be overwritten
EXCLUDE_PATTERN_TIF_SEARCH = r"^(?!.*(T_PMT|" + NEW_PATH_SUF + ")\.tif).*"
############################################

def correct_constant_shift_X_img(img, shift):
    for i, row in enumerate(img[::2]):
        l = i*2
        img[l] = np.roll(row, shift)
    return img

def correct_constant_shift_X(z_stack, shift):
    for z, img in enumerate(z_stack):
        img = correct_constant_shift_X_img(img, shift)
        z_stack[z] = img
    return z_stack

def find_other_tif(file_path):
    folder_path = os.path.dirname(file_path)
    file_list = os.listdir(folder_path)
    tif_files = [filename for filename in file_list if filename.lower().endswith('.tif')]
    return tif_files

def finding_shift(tif_data, shift):
    eval_img = (tif_data[PREVIEW_Z_STACK][PREVIEW_Z]).copy() #is deepcopy here too much? Would copy also be ok?
    eval_img = correct_constant_shift_X_img(eval_img, shift) 
    imshow(tif_data[PREVIEW_Z_STACK][PREVIEW_Z], eval_img)
    while True:
        answer = input('Do you want to proceed with the shift or change it ([y]/n/"number"/help)? ') #how would you write number here?
    #    if not answer:
    #        return shift
    #    if answer.lower() == 'help':
    #        print('Change the shown image by changing PREVIEW_Z_STACK and PREVIEW_Z in the beginning of the code. \nChange the ending of the new file name by changing NEW_PATH_SUF in the code. \nCurrent z stack and z displayed: ' + str(PREVIEW_Z_STACK) + ' ' +str(PREVIEW_Z) + '\nCurrent ending: ' + NEW_PATH_SUF)
    #        continue
    #    else:
    #        try:
    #            shift = int(answer)
    #            eval_img = (tif_data[PREVIEW_Z_STACK][PREVIEW_Z]).copy()
    #            eval_img = correct_constant_shift_X_img(eval_img, shift) 
    #            imshow(tif_data[PREVIEW_Z_STACK][PREVIEW_Z], eval_img)
    #            proceed = input(f'Shift selected = {shift}. Do you want to continue (y/[n])? ')
    #            if proceed.lower() == 'y':
    #                return shift
    #        except Exception as e:
    #            print(f"{answer} is not an integer. Please enter an integer")
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
    new_path = tif_path.replace('.tif', NEW_PATH_SUF + '.tif' )
    skimage.io.imsave(new_path, corrected_data, check_contrast=False)
    return

def shiftingstuff_other(tif_name, shift, tif_path, scan_other):
    if scan_other == True:
        tif_path =  os.path.join(os.path.dirname(tif_path), tif_name)
        tif_data = skimage.io.imread(tif_path)
        shiftingstuff_main(shift, tif_data, tif_path)
    return

def sequential():
    parser = argparse.ArgumentParser()
    parser.add_argument('tif_path', help='Path to the tif-file')
    parser.add_argument('shift', help='Amount of shift')
    args = parser.parse_args()
    tif_path = args.tif_path
    shift = int(args.shift)

    print('Path: \n' + tif_path)
    print('Original Shift: ' + str(shift))

    tif_data = skimage.io.imread(tif_path)

    print('Please close the window after inspecting if the shift value is right in order to proceed.')
    shift = finding_shift(tif_data, shift)
    print('Shift used: ' +str(shift))

    tif_files = find_other_tif(tif_path)    
    tif_names = [tif_file for tif_file in tif_files if re.match(EXCLUDE_PATTERN_TIF_SEARCH, tif_file)]
    print('New tif file(s) found:\n' + "\n".join(tif_names))

    while True:
        answer = input('Do you want to shift the other .tif files in the folder too? ([y]/n/help)')
        if answer.lower() == 'n':
            scan_other = False
            break
        elif answer.lower() == 'help':
            print('You can change the regex pattern in the beginning of the code (EXCLUDE_PATTERN_TIF_SEARCH). \nIf you dont know regex, ask Chat_GPT to generate one for you by giving it examples of file names and then asking it to generate a regex code which excludes the files you want to exclude. \nCurrent expression is: ' + EXCLUDE_PATTERN_TIF_SEARCH)
            exit()
        else:
            scan_other = True
            break
    return shift, tif_data, tif_names, scan_other, tif_path


if __name__ == "__main__":
    shift, tif_data, tif_names, scan_other, tif_path = sequential()
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = []
        futures = [executor.submit(shiftingstuff_other, tif_name, shift, tif_path, scan_other) for tif_name in tif_names]
        futures.append(executor.submit(shiftingstuff_main, shift, tif_data, tif_path))
        results = [future.result() for future in futures]
    print('Done!')
    exit()