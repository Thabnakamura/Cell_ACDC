# Yeast cells segmentation, tracking and cell lineage analysis
Python scripts for yeast cells segmentation, tracking and cell lineage analysis

## steps for running analyses with the code
### 1) split nd2/czi file into standardized folder structure by using the fiji script(s)
here come the instructions for doing so
### 2) Run YeaZ segmentation and tracking
here come the instructions for doing so
### 3) Run the script YeaSTac_GUI_frames.py for correcting segmentation and tracking

#### Navigation and zooming operations:

#### NOTE on Zooming: you can always zoom with the lens button from the navigation toolbar (bottom-left of the GUI). The zooming functionalities explained below are only to simplify and speed up zooming-in and zooming-out operations

- Go to next frame: *&rarr;* or click on "Next frame" button on the GUI
- Go to previous frame: *&larr;* or click on "Prev. frame" button on the GUI

- Quick zoom-in: *left-click* on the left image or right image (double click with left button on centre image has a different function (see below)). Double-click to quickly zoom-in on the clicked area.

- Quick zoom-out to original view: *right-click* double-click anywhere outside of images.

#### Edit Operations:

#### NOTE: Every edit operation can be undone/redone with *ctrl+z / ctrl+y*. There is no limit to the number of operations that can be undone/redone

- Draw a new label: *right-click* on right image. Roughly follow a bright contour to draw a new label (e.g. small bud missed by segmentation)

- Merge labels: *right-click* on centre image. Click on one label and release on second label to marge the two labels.

- Separate labels: *right-click* on centre image. Double-click on label to be separated. If double-click doesn't work, use *b + right-click* to open manual separation window. On the new window separate the labels with a straight line with *right-click*. These two methods will separate the labels with a straight-line. If you need to be more accurate and you have a bright intensity line that divides the labels (zoom into left image), you can draw with *c + right-click* on the left image along that intensity line to separate the labels. It might take more than one pass to fully separate the labels.

- Delete labels: *middle-click* (scrolling wheel) on centre image. Click on the label to be deleted. Alternatively, you can draw a rectangle with *d + middle-click*. All labels touched by the rectangles will be deleted. The rectangle will stay in place and delete labels every time you go to the next frame. To remove the rectangle double click with *middle-click* on the background of centre image.

- Replace label with its convex hull contour image: *left-click* on centre image. Double click on a label to replace it with its hull contour image. Very handy to fill holes, cracks and replace a label with a rounder object.

- Change label ID: *middle-click* on left image. Click on a label and write the new label ID in the pop-up window. You can change multiple labels at once by writing a list of tuples:
e.g. [(1,3), (5, 6)] label ID 1 will become 3 and label ID 5 will become 6.


### 4) Run the script YeaSTaC_GUI_CellCycleAnalysis.py for performing cell cycle analysis
here come the instructions for doing so
