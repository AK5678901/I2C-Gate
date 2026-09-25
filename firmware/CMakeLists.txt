cmake_minimum_required(VERSION 3.13)
# Keep the existing firmware presets and build directory working. The project
# definition lives at the workspace root for Raspberry Pi Pico extension detection.
include("${CMAKE_CURRENT_LIST_DIR}/../CMakeLists.txt")
# CMake requires a literal project() here to avoid inserting an implicit one
# before the included Pico SDK has selected the cross-compilation toolchain.
project(i2c_gate C CXX ASM)
