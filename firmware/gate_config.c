#include "gate_config.h"
#include <string.h>

static uint16_t u16(const uint8_t *p) { return p[0] | (uint16_t)p[1] << 8; }
static uint32_t u32(const uint8_t *p) { return u16(p) | (uint32_t)u16(p + 2) << 16; }
static bool address_ok(unsigned a) { return a >= 8 && a <= 0x77; }

void config_default(config_t *out) {
    memset(out, 0, sizeof(*out));
    out->speed = 100000;
    out->timeout = 25000;
    out->max_write = 256;
    out->fill = 255;
    for (unsigned a = 8; a <= 0x77; ++a) out->address[a] = true;
}

bool config_decode(config_t *out, const uint8_t *data, size_t size) {
    if (size < 14 || data[0] != 1) return false;
    memset(out, 0, sizeof(*out));
    out->speed = u32(data + 1); out->timeout = u32(data + 5);
    out->max_write = u16(data + 9); out->fill = data[11];
    unsigned addresses = data[12]; out->rule_count = data[13];
    if (out->speed < 10000 || out->speed > 400000 || !out->timeout || out->timeout > 1000000 ||
        !out->max_write || out->max_write > GATE_BYTES || !addresses || addresses > 112 ||
        out->rule_count > GATE_RULES || size < 14 + addresses) return false;
    size_t pos = 14;
    for (unsigned i = 0; i < addresses; ++i) {
        unsigned a = data[pos++];
        if (!address_ok(a) || out->address[a]) return false;
        out->address[a] = true;
    }
    // The v1 list is validated for compatibility, but no longer acts as an allowlist.
    for (unsigned a = 8; a <= 0x77; ++a) out->address[a] = true;
    for (unsigned i = 0; i < out->rule_count; ++i) {
        if (size - pos < 7) return false;
        rule_t *r = &out->rules[i];
        r->enabled = data[pos++]; r->phase = data[pos++]; r->action = data[pos++];
        r->address = data[pos++]; r->destination = data[pos++];
        r->predicates = data[pos++]; r->patches = data[pos++];
        if (r->enabled > 1 || r->phase > 2 || (r->action != ACT_MODIFY && r->action != ACT_BLOCK) || r->predicates > GATE_TERMS || r->patches > GATE_TERMS ||
            (r->address != 255 && (!address_ok(r->address) || !out->address[r->address])) ||
            (r->destination != 255 && (!address_ok(r->destination) || r->phase == PH_READ_RESPONSE || r->action == ACT_BLOCK)) ||
            (r->phase == PH_READ_REQUEST && (r->predicates || r->patches)) ||
            (r->action == ACT_MODIFY && !r->patches && r->destination == 255) ||
            (r->action == ACT_BLOCK && r->patches)) return false;
        unsigned max_pred = 0, min_patch = GATE_BYTES;
        for (unsigned j = 0; j < (unsigned)r->predicates + r->patches; ++j) {
            if (size - pos < 4) return false;
            term_t *t = j < r->predicates ? &r->predicate[j] : &r->patch[j - r->predicates];
            t->offset = u16(data + pos); t->value = data[pos + 2]; t->mask = data[pos + 3]; pos += 4;
            if (t->offset >= GATE_BYTES) return false;
            if (j < r->predicates) { if (t->offset > max_pred) max_pred = t->offset; }
            else {
                if (t->offset < min_patch) min_patch = t->offset;
                for (unsigned k = 0; k < j - r->predicates; ++k)
                    if (r->patch[k].offset == t->offset) return false;
            }
        }
        if (r->phase == PH_READ_RESPONSE && r->patches && max_pred > min_patch) return false;
    }
    return pos == size;
}

const rule_t *filter_match(const config_t *cfg, unsigned phase, uint8_t address,
                          const uint8_t *data, size_t count, bool streaming) {
    for (unsigned i = 0; i < cfg->rule_count; ++i) {
        const rule_t *r = &cfg->rules[i];
        if (!r->enabled || r->phase != phase || (r->address != 255 && r->address != address)) continue;
        bool match = true;
        for (unsigned j = 0; j < r->predicates; ++j) {
            const term_t *t = &r->predicate[j];
            if (t->offset >= count || (data[t->offset] & t->mask) != (t->value & t->mask)) { match = false; break; }
        }
        if (!streaming) for (unsigned j = 0; j < r->patches; ++j)
            if (r->patch[j].offset >= count) match = false;
        if (match) return r;
    }
    return NULL;
}

uint8_t filter_byte(const rule_t *r, size_t offset, uint8_t value) {
    if (r && r->action == ACT_MODIFY) for (unsigned j = 0; j < r->patches; ++j) {
        const term_t *t = &r->patch[j];
        if (t->offset == offset) value = (value & (uint8_t)~t->mask) | (t->value & t->mask);
    }
    return value;
}

uint32_t gate_crc32(const uint8_t *data, size_t count) {
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < count; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ ((0u - (crc & 1u)) & 0xedb88320u);
    }
    return ~crc;
}
