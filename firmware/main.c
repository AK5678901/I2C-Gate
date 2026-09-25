#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/multicore.h"
#include "hardware/sync.h"
#include "gpio_bridge.h"
#include "i2c_echo_stub.h"

static uint8_t incoming[65536];
static uint32_t u32(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int main(void) {
    stdio_init_all();
    bridge_init();
    i2c_echo_stub_init();
    multicore_launch_core1(bridge_core1);
    uint8_t header[12];
    size_t pos = 0, length = 0, received = 0;
    uint32_t last = time_us_32(), crc = 0;
    bool binary = false;
    for (;;) {
        bridge_service();
        if ((pos || binary) && (uint32_t)(time_us_32() - last) > 2000000) {
            pos = received = length = 0; binary = false;
            puts("ERR TIMEOUT");
        }
        int c = getchar_timeout_us(0);
        if (c < 0) continue;
        last = time_us_32();
        if (binary) {
            incoming[received++] = (uint8_t)c;
            if (received != length) continue;
            binary = false; pos = 0;
            if (gate_crc32(incoming, length) != crc) { puts("ERR CRC"); continue; }
            if (gate_pending_config >= 0) { puts("ERR BUSY"); continue; }
            int target = 1 - gate_active_config;
            if (!config_decode(&gate_configs[target], incoming, length)) { puts("ERR CONFIG"); continue; }
            __dmb(); gate_pending_config = target;
            uint32_t start = time_us_32();
            while (gate_pending_config >= 0 && (uint32_t)(time_us_32() - start) < 2000000)
                bridge_service();
            if (gate_pending_config >= 0) {
                // Pending remains queued; a timeout is explicitly an unconfirmed result.
                puts("ERR APPLY_PENDING");
            } else printf("OK %08lX\n", (unsigned long)crc);
            continue;
        }
        header[pos++] = (uint8_t)c;
        if (pos == 6 && memcmp(header, "HELLO\n", 6) == 0) {
            puts("I2C-GATE 1 GPIO-EXPERIMENTAL"); pos = 0;
        } else if (pos == 4 && memcmp(header, "I2CG", 4) != 0 && memcmp(header, "HELL", 4) != 0) {
            pos = 0;
        } else if (pos == sizeof(header)) {
            if (memcmp(header, "I2CG", 4) == 0) {
                length = u32(header + 4); crc = u32(header + 8);
                if (length >= 14 && length <= sizeof(incoming)) { binary = true; received = 0; }
                else puts("ERR LENGTH");
            }
            pos = 0;
        }
    }
}
