#include "gate_config.h"
#include "echo_fifo.h"
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif
static config_t config;
EXPORT int decode(const uint8_t *data, size_t size) { return config_decode(&config, data, size); }
EXPORT int evaluate(unsigned phase, uint8_t address, const uint8_t *data, size_t count,
                    int streaming, size_t offset, uint8_t *output) {
    const rule_t *r = filter_match(&config, phase, address, data, count, streaming != 0);
    *output = filter_byte(r, offset, data[offset]);
    return r ? r->action : ACT_PASS;
}
EXPORT uint32_t crc(const uint8_t *data, size_t size) { return gate_crc32(data, size); }

static echo_fifo_t echo;
EXPORT void echo_reset(void) { echo_fifo_init(&echo); }
EXPORT int echo_write(uint8_t value) { return echo_fifo_write(&echo, value); }
EXPORT uint8_t echo_read(void) { return echo_fifo_read(&echo); }
EXPORT size_t echo_count(void) { return echo.count; }
EXPORT uint32_t echo_underflows(void) { return echo.underflows; }
EXPORT uint32_t echo_overflows(void) { return echo.overflows; }
EXPORT size_t echo_capacity(void) { return ECHO_FIFO_CAPACITY; }
