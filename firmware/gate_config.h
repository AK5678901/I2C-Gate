#ifndef GATE_CONFIG_H
#define GATE_CONFIG_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define GATE_RULES 64
#define GATE_TERMS 64
#define GATE_BYTES 4096
enum { PH_WRITE, PH_READ_REQUEST, PH_READ_RESPONSE };
enum { ACT_MODIFY = 1, ACT_BLOCK = 2 };
typedef enum { ACK_HOST, ACK_FORCE, NACK_FORCE } ack_mode_t;
typedef struct { uint16_t offset; uint8_t value, mask; } term_t;
typedef struct {
    uint8_t enabled, phase, action, address, destination, predicates, patches;
    term_t predicate[GATE_TERMS], patch[GATE_TERMS];
    uint8_t ack, has_write, write_address, write_predicates;
    uint16_t nack_at; // UINT16_MAX = NACK as soon as the rule matches.
    term_t write_predicate[GATE_TERMS];
} rule_t;
typedef struct {
    uint32_t speed, timeout;
    uint16_t max_write;
    uint8_t fill, rule_count;
    bool address[128];
    rule_t rules[GATE_RULES];
} config_t;
// Original (unmodified) bytes. Valid only for the duration of a callback.
typedef struct {
    bool valid;
    uint8_t address;
    const uint8_t *data;
    size_t count;
} write_context_t;
typedef struct { bool block; uint8_t destination; } address_result_t;
typedef struct { bool block, modify; uint8_t value; ack_mode_t ack; } data_result_t;
typedef address_result_t (*address_callback_t)(const config_t *, uint8_t, bool,
                                               const write_context_t *);
typedef data_result_t (*data_callback_t)(const config_t *, uint8_t, bool,
                                        const uint8_t *, size_t, const write_context_t *);
address_result_t filter_address(const config_t *, uint8_t, bool, const write_context_t *);
data_result_t filter_data(const config_t *, uint8_t, bool, const uint8_t *, size_t,
                          const write_context_t *);
void config_default(config_t *out);
bool config_decode(config_t *out, const uint8_t *data, size_t size);
const rule_t *filter_match(const config_t *cfg, unsigned phase, uint8_t address,
                          const uint8_t *data, size_t count, bool streaming);
uint8_t filter_byte(const rule_t *rule, size_t offset, uint8_t value);
uint32_t gate_crc32(const uint8_t *data, size_t count);
#endif
