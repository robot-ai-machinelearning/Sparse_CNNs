'''
   This module implements Vote3Deep, a neural network algorithm specialized
   in recognizing 3D shapes from point clouds, using only CPUs in parallel.

   The main characteristics are:
   - Use of sparcity from point clouds to avoid using dense matrixes
   - Use of sparcity to implement convolutions exploiting parallelism
   - Use or ReLus and L1 sparsity penalty to encourage data sparcity
   - Use of voxels to reduce density of inputs to the network
   - Use of vectors composed of 3D features from points in each voxel
   - Features are: 3 shape factors (from eigenvectors), mean and variance of reflectance
'''
from __future__ import print_function
import os
import glob
import argparse
import numpy as np
import torch
import torch.nn as nn

from train import Train_Model2
from helper import load_and_sort_paths


# the network Hyper parameters refer to section V C
# The control parameters parsing from the command line
ap = argparse.ArgumentParser(description='Implementation of Vote3Deep training module')
ap.add_argument("-f", "--fvthresh", type=int, required=True,
                help="type [int] \n \
                [INFO]The min number of points required to \
                extract a feature vector")
ap.add_argument("-w", "--wdthresh", type=int, required=True,
                help="type [int] \n \
                [INFO] The min number of features for a window")
ap.add_argument("-res", "--resolution", type=float, required=False,
                default=0.2,
                help="type [float][Optional] \n \
                [INFO] The resolution of the voxel in the pointcloud")
ap.add_argument("-b", "--batchsize_hnm", type=int, required=True,
                help="type [int] \n \
                [INFO] The hard negative mining batchsize can be a value \
                close to the the number of cores on the sytem running it")
ap.add_argument("-i", "--input_path", required=True,
                help="type [str] \n \
                [INFO] The path to the input data folder")
ap.add_argument("-r", "--resume_train", type=bool, default=False,
                help="type [True/False] \n \
                [INFO] if to resume training from a set of available weights ")
ap.add_argument("-epoch", "--epoch", type=int, required=False,
                help="type [int] \n \
                [INFO]The epoch from which to begin the training")
args = vars(ap.parse_args())
# TODO: include a cfg file in the folder where the weights are stored. If the cfg present
#       load the parameters from this file and resume the training from here (or at least test
#       for presence or absence of this file to infer whether we should start from scratch)
# TODO: Also include a argument for the working directory. where we store the weights,cfg,etc.
####################################################################################################
fvthresh = args["fvthresh"] # min number of points for feature extraction
wdthresh = args["wdthresh"] # min number of feature vectors per window
# refer to the voting for voting in online pc(Wang and Posner) Section 7 C
resolution = args["resolution"]
# The hnm batch size
batchsizehnm = args["batchsize_hnm"] # make it equal to the number of cores -2 on the system
####################################################################################################

####################################################################################################
####################################################################################################
ip_path = args["input_path"]
# paths to 80:20 split data (load the train data)
pcs_orig = ip_path+"/kittisplit/train/bin/*.bin"
labels_orig = ip_path+"/kittisplit/train/labels/*.txt"
calibs_orig = ip_path+"/kittisplit/train/calibs/*.txt"
####################################################################################################
# TODO: Test that objects belongs to a dictionary of valid objects (i.e. Cyclist Car Pedestrian)
#######################################################
objects = "Pedestrian" # must belong to: Cyclist Car Pedestrian
####################################################################################################
# the full point cloud and labels files
########################################################

full_pcs_bin_paths = load_and_sort_paths(pcs_orig)

full_pcs_labels_paths = load_and_sort_paths(labels_orig)

full_pcs_calibs_paths = load_and_sort_paths(calibs_orig)

# full_pcs_bin_paths = full_pcs_bin_paths[:10]
# full_pcs_labels_paths = full_pcs_labels_paths[:10]
# full_pcs_calibs_paths = full_pcs_calibs_paths[:10]

########################################################

# paths to positive and negative crops of the data
########################################################
folder = str(fvthresh)+str(wdthresh)
car_positives = ip_path+"/crops/train/positive/"+objects+"/*.bin"
neg = ip_path+"/crops/train/negative/"+objects+"/*.bin"
# hnmpath,weightspath,lossvalues path
########################################################
# hnm_path
# The path to this will be separate for every new
# threshold we have.
hnm_path = ip_path+"/train_data/"+objects+"/2layer/"+folder+"/hnm_data/"
# The path where we store the scores and loss values
values_path_dir = ip_path+"/train_data/"+objects+"/2layer/"+folder+"/lossvalues/"
values_file = "scoreserror.txt"
# Weghts path
weights_path = ip_path+"/train_data/"+objects+"/2layer/"+folder+"/weights/"
#######################################################
if not os.path.exists(values_path_dir):
    os.makedirs(values_path_dir)
if not os.path.exists(hnm_path):
    os.makedirs(hnm_path)
######################################################

# other factors
###################################################
batchsize = 16 # should be kept constant given by paper
sgd_momentum = 0.9 # Stochastic grdient decsent
l2_weightdecay = 0.0001
lr = 0.001# learning rate
pad = 1
epochs = 100
filter_size = [3, 3, 3] # Refer to fig 3 in vote3deep
receptive_field = [int(2.0/0.2), int(1.0/0.2), int(2.0/0.2)] # # pedestrian
# 95 percentile Receptive field size of the object (Refer to Section 3)
# Receptive_Field = [int(2.0/0.2), int(1.0/0.2), int(2.0/0.2)] # cyclist
angular_bins = 8 # angular bins in 360/45
x = (0, 80)
y = (40, 40)
z = (2.5, 1.5)
# TODO: get rid of the parameters number  of filters and the number of channels
num_filters1 = 8 # Refer to fig 3 in vote3deep
channels1 = 6 # number of features for each grid cell
num_filters2 = 8 # Refer to fig 3 in vote3deep
channels2 = 8 # number of features for each grid cell
# we can collect them from the shape of weights.
######################################################
if not args["resume_train"]:
    # remove the old scoreerror values if exist
    if os.path.exists(values_path_dir+values_file):
        os.remove(values_path_dir+values_file)
        print("File Removed!")
    values_path = values_path_dir+values_file
    ################################################################################################
    ################################################################################################
    ################################# No pretrained weights ########################################
    #####      init the weights and biases based on Delving deep into Rectifiers(He et al.)
    ################################################################################################
    ########################################################

    if not os.path.exists(weights_path):
        os.makedirs(weights_path)
    ########################################################

    # fig 3 from paper w1
    wght1 = torch.empty(num_filters1, channels1, filter_size[0], filter_size[1], filter_size[2])
    weights1 = nn.init.kaiming_normal_(wght1, mode='fan_in', nonlinearity='relu')
    weights1 = weights1.numpy()
    weights1 = weights1.T
    b1 = np.zeros(num_filters1)

    # fig 3 from paper w1
    wght2 = torch.empty(num_filters2, channels2, filter_size[0], filter_size[1], filter_size[2])
    weights2 = nn.init.kaiming_normal_(wght2, mode='fan_in', nonlinearity='relu')
    weights2 = weights2.numpy()
    weights2 = weights2.T
    b2 = np.zeros(num_filters2)

    # fig 3 from paper w1
    wght3 = torch.empty((receptive_field[0])*(receptive_field[1])*(receptive_field[2])*num_filters2, 1)
    weights3 = nn.init.kaiming_normal_(wght3, mode='fan_in', nonlinearity='relu')
    weights3 = weights3.numpy()
    b3 = np.zeros(1) # page 4 b=0
    curr_epoch = 0

else:
    # if scoreerror file already exist append to it
    values_path = values_path_dir+values_file
    ################################################################################################
    ####################### IF YOU HAVE PRETRAINED WEIGHTS #########################################
    ####################### READ COMMENT ABOVE, AND COMMENT THE NEXT LINES
    ####################### Training from a set of known pretrained weights.  ######################
    epoch = args["epoch"]
    file_name = "epoch_"+str(epoch)+".weights.npz"
    ################################################################################################
    wghts = np.load(weights_path+file_name)

    weights1 = wghts['w1']
    weights2 = wghts['w2']
    weights3 = wghts['w3']
    b1 = wghts['b1']
    b2 = wghts['b2']
    b3 = wghts['b3']


train_obj = Train_Model2(batchsize, full_pcs_bin_paths, full_pcs_labels_paths, \
    full_pcs_calibs_paths, car_positives, neg, hnm_path, resolution, epochs, lr, sgd_momentum, \
    l2_weightdecay, angular_bins, weights1, weights2, weights3, b1, b2, b3, receptive_field, pad, \
    curr_epoch, channels1, channels2, num_filters1, num_filters2, x, y, z, fvthresh, wdthresh, \
    batchsizehnm, objects, weights_path, values_path)
train_obj.train()
