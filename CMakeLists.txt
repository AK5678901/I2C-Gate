cmake_minimum_required(VERSION 3.13)

# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.3.0)
set(toolchainVersion 15_2_Rel1)
set(picotoolVersion 2.3.0)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
set(PICO_BOARD pico CACHE STRING "Board type")

if(NOT PICO_SDK_PATH)
    set(PICO_SDK_PATH "$ENV{PICO_SDK_PATH}")
endif()
include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")
project(i2c_gate C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()
set(FIRMWARE_DIR "${CMAKE_CURRENT_LIST_DIR}/firmware")
add_executable(i2c_gate
    ${FIRMWARE_DIR}/main.c
    ${FIRMWARE_DIR}/gate_config.c
    ${FIRMWARE_DIR}/gpio_bridge.c
    ${FIRMWARE_DIR}/i2c_echo_stub.c
    ${FIRMWARE_DIR}/echo_fifo.c
)
target_compile_options(i2c_gate PRIVATE -Wall -Wextra -Werror -O2)
target_compile_definitions(i2c_gate PRIVATE PICO_STDIO_USB_STDOUT_TIMEOUT_US=1000)
target_link_libraries(i2c_gate pico_stdlib pico_multicore hardware_sync hardware_clocks pico_i2c_slave)
pico_enable_stdio_usb(i2c_gate 1)
pico_enable_stdio_uart(i2c_gate 0)
# Core 1 polling and filter paths must not stall on XIP cache misses.
pico_set_binary_type(i2c_gate copy_to_ram)
pico_add_extra_outputs(i2c_gate)
