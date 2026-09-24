#include "gate_config.h"
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
