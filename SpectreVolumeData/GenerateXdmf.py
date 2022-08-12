#!/usr/bin/env python

# Distributed under the MIT License.
# See LICENSE.txt for details.

import glob
import h5py
import logging
import numpy as np
import sys


def generate_xdmf(file_prefix, output, subfile_name, start_time, stop_time,
                  stride, coordinates):
    """
    Generate one XDMF file that ParaView and VisIt can use to load the data
    out of the HDF5 files.
    """
    if sys.version_info < (3, 0):
        logging.warning("You are attempting to run this script with "
                        "python 2, which is deprecated. GenerateXdmf.py might "
                        "hang or run very slowly using python 2. Please use "
                        "python 3 instead.")

    h5files = [(h5py.File(filename, 'r'), filename)
               for filename in glob.glob(file_prefix + "[0-9]*.h5")]

    assert len(h5files) > 0, "No H5 files with prefix '{}' found.".format(
        file_prefix)

    element_data = h5files[0][0].get(subfile_name + '.vol')
    if element_data is None:
        raise ValueError(("Could not open subfile name '{}.vol'. Available "
                          "subfiles: {}").format(subfile_name,
                                                 h5files[0][0].keys()))
    temporal_ids_and_values = [(x,
                                element_data.get(x).attrs['observation_value'])
                               for x in element_data.keys()]
    temporal_ids_and_values.sort(key=lambda x: x[1])

    xdmf_output = "<?xml version=\"1.0\" ?>\n" \
        "<!DOCTYPE Xdmf SYSTEM \"Xdmf.dtd\">\n" \
        "<Xdmf Version=\"2.0\">\n" \
        "<Domain>\n" \
        "<Grid Name=\"Evolution\" GridType=\"Collection\" " \
        "CollectionType=\"Temporal\">\n"

    # counter used to enforce the stride
    stride_counter = 0
    for id_and_value in temporal_ids_and_values:
        if id_and_value[1] < start_time:
            continue
        if id_and_value[1] > stop_time:
            break

        stride_counter += 1
        if (stride_counter - 1) % stride != 0:
            continue

        h5temporal = element_data.get(id_and_value[0])
        xdmf_output += "  <Grid Name=\"Grids\" GridType=\"Collection\">\n"
        xdmf_output += "    <Time Value=\"%1.14e\"/>\n" % (id_and_value[1])
        # The following while loop will execute once, unless visualizing
        # a 2D surface in 3D space. In that case, the loop will execute
        # twice, with the second time through taking care of filling in the
        # poles.
        done = False
        filling_poles_this_iteration = False
        while not done:
            # loop over each h5 file
            for h5file in h5files:
                h5temporal = h5file[0].get(subfile_name + '.vol').get(
                    id_and_value[0])
                # Skip this file if the observation does not exist in it.
                # Usually this is because the program crashed before
                # writing it.  Data in other files will still be processed
                # to allow viewing the part that was written.
                if not h5temporal:
                    continue
                # Make sure the coordinates are found in the file. We assume
                # there should always be an x-coordinate.
                assert coordinates + '_x' in h5temporal, (
                    "No '{}_x' dataset found in file '{}'. Existing datasets "
                    "with 'Coordinates' in their name: {}").format(
                        coordinates, h5file[1],
                        list(
                            map(
                                lambda coords_name: coords_name.strip('_x'),
                                filter(
                                    lambda dataset_name: 'Coordinates' in
                                    dataset_name and '_x' in dataset_name,
                                    h5temporal.keys()))))
                dimensionality = 3
                is_2d_surface_in_3d_space = False
                # If pole_connectivity is in the list of keys, then this is 2D
                # surface data in 3D space. Otherwise, if there are no
                # z-coordinates then assume the data is 2d. If in the future
                # this assumption is invalid on datasets we will need an extra
                # command line argument.
                if 'pole_connectivity' in list(h5temporal.keys()):
                    is_2d_surface_in_3d_space = True
                elif coordinates + '_z' not in list(h5temporal.keys()):
                    dimensionality = 2

                # Compute the extents in the x, y, (and z) logical directions
                # for each element in the dataset.
                extents = h5temporal.get("total_extents")
                extents_x = np.array([
                    extents[i] for i in range(extents.size)
                    if i % dimensionality == 0
                ])
                extents_y = np.array([
                    extents[i] for i in range(extents.size)
                    if i % dimensionality == 1
                ])

                # So that we can have more generic code, set that we have 1
                # grid point in z in 2d. For a 2D surface in 3D space, we have
                # 1 grid point in z, extents_x == l+1 and extents_y == 2l+1.
                extents_z = np.array([1])
                if not is_2d_surface_in_3d_space and dimensionality == 3:
                    extents_z = np.array([
                        extents[i] for i in range(extents.size)
                        if i % dimensionality == 2
                    ])

                # The total number of points is the sum of the tensor product
                # of the 1d number of grid points.
                numpoints = sum(extents_x * extents_y * extents_z)
                # Because we can't have zero cells if rendering a 2d surface,
                # return 1 in that case.
                number_of_cells = 1
                if filling_poles_this_iteration:
                    # Number_of_cells == pole_connectivity size / 3
                    # because we are using triangles to fill the poles
                    number_of_cells = h5temporal.get(
                        'pole_connectivity').size / 3
                elif is_2d_surface_in_3d_space:
                    # Number_of_cells == l * (2*l + 1)
                    number_of_cells = sum((extents_x - 1) * extents_y)
                else:
                    number_of_cells = sum(
                        (extents_x - 1) * (extents_y - 1) *
                        (extents_z - 1 if dimensionality == 3 else 1))

                topology = "Hexahedron"
                vertices = 8
                connectivity_path = "connectivity"
                if filling_poles_this_iteration:
                    topology = "Triangle"
                    vertices = 3
                    connectivity_path = "pole_connectivity"
                elif is_2d_surface_in_3d_space or dimensionality == 2:
                    topology = "Quadrilateral"
                    vertices = 4

                def data_item(data_type):
                    assert (
                        data_type == np.dtype('float64')
                        or data_type == np.dtype('float32')
                    ), ("Data type must be either a 32-bit or 64-bit float but"
                        " got " + str(data_type))
                    return (
                        "        <DataItem Dimensions=\" %d\" "
                        "NumberType=\"%s\" Precision=\"8\" Format=\"HDF5\">\n"
                        % (numpoints, "Double"
                           if data_type == np.dtype('float64') else "Float"))

                # Set up vectors
                if dimensionality == 3:
                    data_item_vec = (
                        "        <DataItem Dimensions=\" %d 3\" "
                        "ItemType = \"Function\" Function = \"JOIN($0,$1,$2)\">"
                        "\n" % (numpoints))
                else:
                    # In 2d we still need a 3d dataset to have a vector because
                    # ParaView only supports 3d vectors. We deal with this by
                    # making the z-component all zeros.
                    data_item_vec = ("        <DataItem Dimensions=\" %d 3\" "
                                     "ItemType = \"Function\" "
                                     "Function = \"JOIN($0,$1, 0 * $1)\">\n" %
                                     (numpoints))

                # Configure grid location
                Grid_path = ("          {}:/".format(h5file[1]) +
                             subfile_name + ".vol/{}".format(id_and_value[0]))
                xdmf_output += (
                    "    <Grid Name=\"%s\" GridType=\"Uniform\">\n" %
                    (h5file[1]))
                # Write topology information. In 3d we write a hexahedral
                # topology (this may need to change when we have DG-subcell),
                # while in 2d we write a quadrilateral topology (and may also
                # need to change when we have DG-subcell).
                if dimensionality == 3:
                    xdmf_output += ("      <Topology TopologyType=\"%s\" "
                                    "NumberOfElements=\"%d\">\n" %
                                    (topology, number_of_cells))
                else:
                    xdmf_output += ("      <Topology "
                                    "TopologyType=\"quadrilateral\" "
                                    "NumberOfElements=\"%d\">\n" %
                                    (number_of_cells))

                # The ternary for 3d returns 8 because that's how many vertices
                # a hexahedron has, and 4 in 2d because that's how many vertices
                # a quadrilateral has.
                xdmf_output += ("        <DataItem Dimensions=\"%d %d\" "
                                "NumberType=\"Int\" Format=\"HDF5\">\n" %
                                (number_of_cells, vertices))
                xdmf_output += (Grid_path + "/" + connectivity_path + "\n"
                                "        </DataItem>\n      </Topology>\n")

                # Write geometry/coordinates. The X_Y_Z and X_Y means that the
                # x, y, and z coordinates are stored in separate datasets,
                # rather than something like interleaved.
                if dimensionality == 3:
                    xdmf_output += "      <Geometry Type=\"X_Y_Z\">\n"
                else:
                    xdmf_output += "      <Geometry Type=\"X_Y\">\n"
                xdmf_output += (
                    data_item(h5temporal.get(coordinates + '_x').dtype) +
                    Grid_path + "/" + coordinates +
                    "_x\n        </DataItem>\n")
                xdmf_output += (
                    data_item(h5temporal.get(coordinates + '_y').dtype) +
                    Grid_path + "/" + coordinates +
                    "_y\n        </DataItem>\n")
                if dimensionality == 3:
                    xdmf_output += (
                        data_item(h5temporal.get(coordinates + '_z').dtype) +
                        Grid_path + "/" + coordinates +
                        "_z\n        </DataItem>\n")
                xdmf_output += "      </Geometry>\n"
                # Everything that isn't a coordinate is a "component"
                components = list(h5temporal.keys())
                components.remove(coordinates + '_x')
                components.remove(coordinates + '_y')
                if dimensionality == 3:
                    # In 2d we cannot read any z-coordinates because they
                    # were never written.
                    components.remove(coordinates + '_z')
                components.remove('connectivity')
                if is_2d_surface_in_3d_space:
                    components.remove('pole_connectivity')
                components.remove('total_extents')
                components.remove('grid_names')
                components.remove('bases')
                components.remove('quadratures')

                # Write the tensors that are to be visualized.
                for component in components:
                    if component.endswith("_x"):
                        # Write a vector using the three components that make up
                        # the vector (i.e. v_x, v_y, v_z)
                        vector = component[:-2]
                        xdmf_output += (
                            "      <Attribute Name=\"%s\" "
                            "AttributeType=\"Vector\" Center=\"Node\">\n" %
                            (vector))
                        xdmf_output += data_item_vec

                        index_list = ["_x", "_y"]
                        if dimensionality == 3:
                            index_list.append("_z")
                        for index in index_list:
                            xdmf_output += (data_item(
                                h5temporal.get(vector + index).dtype) +
                                            Grid_path + "/%s" % (vector) +
                                            index + "\n" +
                                            "        </DataItem>\n")
                        xdmf_output += "        </DataItem>\n"
                        xdmf_output += "      </Attribute>\n"
                    elif (component.endswith("_y")
                          or component.endswith("_z")):
                        # The component is a y or z component of a vector
                        # it will be processed with the x component
                        continue
                    else:
                        # If the component is not part of a vector,
                        # write it as a scalar
                        xdmf_output += (
                            "      <Attribute Name=\"%s\" "
                            "AttributeType=\"Scalar\" Center=\"Node\">\n" %
                            (component))
                        xdmf_output += data_item(
                            h5temporal.get(component).dtype) + Grid_path + (
                                "/%s\n" % component) + "        </DataItem>\n"
                        xdmf_output += "      </Attribute>\n"
                xdmf_output += "    </Grid>\n"

            if is_2d_surface_in_3d_space:
                if filling_poles_this_iteration:
                    done = True
                    # close time grid
                    xdmf_output += "  </Grid>\n"
                else:
                    filling_poles_this_iteration = True
            else:
                done = True
                # close time grid
                xdmf_output += "  </Grid>\n"

    xdmf_output += "</Grid>\n</Domain>\n</Xdmf>"

    for h5file in h5files:
        h5file[0].close()

    with open(output + ".xmf", "w") as xmf_file:
        xmf_file.write(xdmf_output)


def parse_args():
    """
    Parse the command line arguments
    """
    import argparse as ap
    parser = ap.ArgumentParser(
        description="Generate XDMF file for visualizing SpECTRE data. "
        "To load the XDMF file in ParaView you must choose the 'Xdmf Reader', "
        "NOT 'Xdmf3 Reader'",
        formatter_class=ap.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '--file-prefix',
        required=True,
        help="The common prefix of the H5 volume files to load, excluding "
        "the node number integer(s)")
    parser.add_argument(
        '--output',
        '-o',
        required=True,
        help="Output file name, an xmf extension will be added")
    parser.add_argument(
        '--subfile-name',
        '-d',
        required=True,
        help="Name of the volume data subfile in the H5 files, excluding the "
        "'.vol' extension")
    parser.add_argument("--stride",
                        default=1,
                        type=int,
                        help="View only every stride'th time step")
    parser.add_argument(
        "--start-time",
        default=0.0,
        type=float,
        help="The earliest time at which to start visualizing. The start-time "
        "value is included.")
    parser.add_argument(
        "--stop-time",
        default=1e300,
        type=float,
        help="The time at which to stop visualizing. The stop-time value is "
        "not included.")
    parser.add_argument("--coordinates",
                        default="InertialCoordinates",
                        help="The coordinates to use for visualization")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    input_args = parse_args()
    generate_xdmf(**vars(input_args))
