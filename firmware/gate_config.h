#ifndef GATE_CONFIG_H
#define GATE_CONFIG_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define GATE_RULES 64
#define GATE_TERMS 64
#define GATE_BYTES 4096
enum { PH_WRITE, PH_READ_REQUEST, PH_READ_RESPONSE };
enum { ACT_PASS, ACT_MODIFY, ACT_BLOCK };
typedef struct { uint16_t offset; uint8_t value, mask; } term_t;
typedef struct {
    uint8_t enabled, phase, action, address, destination, predicates, patches;
    term_t predicate[GATE_TERMS], patch[GATE_TERMS];
} rule_t;
typedef struct {
    uint32_t speed, timeout;
    uint16_t max_write;
    uint8_t fill, rule_count;
    bool address[128];
    rule_t rules[GATE_RULES];
} config_t;
bool config_decode(config_t *out, const uint8_t *data, size_t size);
const rule_t *filter_match(const config_t *cfg, unsigned phase, uint8_t address,
                          const uint8_t *data, size_t count, bool streaming);
uint8_t filter_byte(const rule_t *rule, size_t offset, uint8_t value);
uint32_t gate_crc32(const uint8_t *data, size_t count);
#endif
