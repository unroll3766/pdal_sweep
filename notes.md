PDAL Sweep notes
- When user selects vary, the check box moves from the right of the parameter box to the left. It should stay fixed on the right. When a user imports a JSON, it correctly stays on the right.
- User should be able to press a back button to return to a sweep results after examining a run in detail
- 3D view right-drag to zoom changes the grid overlay and the point cloud extent, but doesn't actually change the size of the point cloud. It needs to actually zoom in on the points.
- 3D view could also use a point size setting (larger value = larger points)
- In results viewer, user should be able to swap the preview to the output of their choice (e.g. top down, DTM, cross section, etc.)
- DTM results look a lot worse, I think the extent is shrunk down to the valid ground. For reference, the DTM should be the same extent as the plot, even if there's no data. The render of the DTM is smaller than the full extent of the scale bar. (see screenshot)
