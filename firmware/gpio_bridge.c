// Experimental GPIO bridge: validate timing with a logic analyzer before use.
// Core 1 exclusively owns upstream pins; core 0 owns downstream pins.
#include "gpio_bridge.h"
#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/structs/sio.h"
#include "hardware/sync.h"

config_t gate_configs[2];
volatile int gate_active_config = 0;
volatile int gate_pending_config = -1;
volatile uint32_t gate_faults = 0;

enum { CMD_NONE, CMD_WRITE, CMD_READ_START, CMD_READ_BYTE, CMD_READ_ACK, CMD_STOP };
enum { RX_STOP = -1, RX_START = -2, RX_TIMEOUT = -3 };
static struct {
    volatile unsigned command;
    volatile int result;
    const config_t *cfg;
    uint8_t address, value;
    uint8_t *data;
    size_t count;
    bool restart;
} job;
static uint8_t write_data[GATE_BYTES], read_prefix[GATE_BYTES];
static uint32_t deadline, half_cycles;
static bool down_open;
static bool up_failed;

static inline bool high(unsigned pin) { return (sio_hw->gpio_in & (1u << pin)) != 0; }
static inline void low(unsigned pin) { sio_hw->gpio_oe_set = 1u << pin; }
static inline void release(unsigned pin) { sio_hw->gpio_oe_clr = 1u << pin; }
static inline bool expired(uint32_t end) { return (int32_t)(time_us_32() - end) >= 0; }
static void half(void) { busy_wait_at_least_cycles(half_cycles); }

static bool down_high(void) {
    release(DOWN_SCL);
    while (!high(DOWN_SCL)) if (expired(deadline)) return false;
    half();
    return !expired(deadline);
}

static bool down_stop(void) {
    low(DOWN_SCL); low(DOWN_SDA); half();
    bool ok = down_high();
    release(DOWN_SDA); half(); down_open = false;
    if (!ok) release(DOWN_SCL);
    return ok;
}

static bool down_start(void) {
    release(DOWN_SDA); half();
    if (!down_high()) return false;
    if (!high(DOWN_SDA)) return false;
    low(DOWN_SDA); half(); low(DOWN_SCL); half(); down_open = true;
    return true;
}

static bool down_bit(bool bit) {
    if (bit) release(DOWN_SDA); else low(DOWN_SDA);
    half();
    if (!down_high()) return false;
    low(DOWN_SCL); half();
    return true;
}

static int down_sample(void) {
    release(DOWN_SDA); half();
    if (!down_high()) return -1;
    int bit = high(DOWN_SDA);
    low(DOWN_SCL); half();
    return bit;
}

static bool down_write(uint8_t value) {
    for (unsigned i = 0; i < 8; ++i) {
        if (!down_bit(value & 0x80)) return false;
        value <<= 1;
    }
    return down_sample() == 0;
}

void bridge_service(void) {
    unsigned command = job.command;
    if (command == CMD_NONE) return;
    __dmb();
    const config_t *cfg = job.cfg;
    deadline = time_us_32() + cfg->timeout;
    half_cycles = (clock_get_hz(clk_sys) + 2 * cfg->speed - 1) / (2 * cfg->speed);
    int result = 0;
    if (command == CMD_WRITE) {
        const rule_t *r = filter_match(cfg, PH_WRITE, job.address, job.data, job.count, false);
        if (r && r->action == ACT_BLOCK) {
            if (down_open) down_stop();
            result = 2; // Prevent a related READ from using stale device state.
        } else {
            uint8_t addr = r && r->destination != 255 ? r->destination : job.address;
            bool ok = down_start() && down_write(addr << 1);
            for (size_t i = 0; ok && i < job.count; ++i)
                ok = down_write(filter_byte(r, i, job.data[i]));
            if (!job.restart || !ok) ok = down_stop() && ok;
            result = ok ? 0 : -1;
        }
    } else if (command == CMD_READ_START) {
        result = down_start() && down_write((job.address << 1) | 1) ? 0 : -1;
    } else if (command == CMD_READ_BYTE) {
        for (unsigned i = 0; i < 8; ++i) {
            int bit = down_sample();
            if (bit < 0) { result = -1; break; }
            result = (result << 1) | bit;
        }
        // SCL remains LOW: the ninth clock waits for the upstream host's ACK/NACK.
    } else if (command == CMD_READ_ACK) {
        result = down_bit(job.value != 0) ? 0 : -1;
        release(DOWN_SDA);
    } else if (command == CMD_STOP) {
        result = down_open && !down_stop() ? -1 : 0;
    }
    if (result < 0) {
        release(DOWN_SDA); release(DOWN_SCL); down_open = false;
        ++gate_faults;
    }
    job.result = result;
    __dmb();
    job.command = CMD_NONE;
}

static int join_job(void) {
    uint32_t end = time_us_32() + (job.cfg ? job.cfg->timeout : 25000);
    while (job.command != CMD_NONE) {
        if (expired(end)) {
            // Release the host on timeout, but retain the mailbox until core 0 finishes.
            release(UP_SDA); release(UP_SCL); up_failed = true;
        }
    }
    __dmb();
    return up_failed ? -1 : job.result;
}

static void submit(unsigned command, const config_t *cfg, uint8_t address,
                   uint8_t value, uint8_t *data, size_t count, bool restart) {
    job.cfg = cfg; job.address = address; job.value = value;
    job.data = data; job.count = count; job.restart = restart;
    __dmb(); job.command = command;
}

static int execute(unsigned command, const config_t *cfg, uint8_t address, uint8_t value) {
    join_job(); submit(command, cfg, address, value, NULL, 0, false);
    return join_job();
}

static bool up_wait(unsigned pin, bool level, uint32_t end) {
    while (high(pin) != level) if (expired(end)) return false;
    return true;
}

// Receives eight bits OR a START/STOP boundary. Return with SCL held low on a byte.
static int up_receive(const config_t *cfg) {
    uint32_t end = time_us_32() + cfg->timeout;
    uint32_t previous = sio_hw->gpio_in;
    unsigned bits = 0, value = 0;
    release(UP_SCL);
    for (;;) {
        uint32_t pins = sio_hw->gpio_in;
        if ((pins & previous & (1u << UP_SCL)) && ((pins ^ previous) & (1u << UP_SDA)))
            return pins & (1u << UP_SDA) ? RX_STOP : RX_START;
        if (!(previous & (1u << UP_SCL)) && (pins & (1u << UP_SCL))) {
            value = (value << 1) | ((pins >> UP_SDA) & 1);
            ++bits;
        }
        if ((previous & (1u << UP_SCL)) && !(pins & (1u << UP_SCL)) && bits == 8) {
            low(UP_SCL); return (int)value;
        }
        previous = pins;
        if (expired(end)) return RX_TIMEOUT;
    }
}

static bool up_ack(const config_t *cfg, bool ack) {
    if (up_failed) return false;
    if (ack) low(UP_SDA); else release(UP_SDA);
    uint32_t end = time_us_32() + cfg->timeout;
    busy_wait_at_least_cycles(32);
    release(UP_SCL);
    if (!up_wait(UP_SCL, true, end) || !up_wait(UP_SCL, false, end)) return false;
    low(UP_SCL); release(UP_SDA);
    return true;
}

// Return 0=host ACK, 1=host NACK, -1=timeout. No additional downstream read occurs here.
static int up_send(const config_t *cfg, uint8_t byte) {
    if (up_failed) return -1;
    uint32_t end = time_us_32() + cfg->timeout;
    for (unsigned i = 0; i < 8; ++i) {
        if (byte & 0x80) release(UP_SDA); else low(UP_SDA);
        byte <<= 1;
        busy_wait_at_least_cycles(32);
        release(UP_SCL);
        if (!up_wait(UP_SCL, true, end) || !up_wait(UP_SCL, false, end)) return -1;
        low(UP_SCL);
    }
    release(UP_SDA); release(UP_SCL);
    if (!up_wait(UP_SCL, true, end)) return -1;
    int nack = high(UP_SDA);
    if (!up_wait(UP_SCL, false, end)) return -1;
    low(UP_SCL);
    return nack;
}

// Watch an unaddressed transaction without ACKing any byte.
static int up_boundary(const config_t *cfg) {
    int result;
    do {
        result = up_receive(cfg);
        if (result >= 0 && !up_ack(cfg, false)) return RX_TIMEOUT;
    } while (result >= 0);
    return result;
}

static void activate_pending(void) {
    int pending = gate_pending_config;
    if (pending >= 0 && job.command == CMD_NONE) {
        __dmb(); gate_active_config = pending;
        __dmb(); gate_pending_config = -1;
    }
}

static void wait_start(void) {
    release(UP_SDA); release(UP_SCL);
    uint32_t previous = sio_hw->gpio_in;
    for (;;) {
        uint32_t pins = sio_hw->gpio_in;
        if ((pins & previous & (1u << UP_SCL)) &&
            (previous & (1u << UP_SDA)) && !(pins & (1u << UP_SDA))) return;
        previous = pins;
        activate_pending();
    }
}

void bridge_core1(void) {
    save_and_disable_interrupts();
    for (;;) {
        wait_start();
        up_failed = false;
        const config_t *cfg = &gate_configs[gate_active_config];
        bool blocked_chain = false;
        bool continuation = false;
        int boundary = RX_START;
        while (boundary == RX_START) {
            int address_byte = up_receive(cfg);
            if (address_byte < 0) { boundary = address_byte; break; }
            // A previous buffered WRITE may still be finishing on core 0.
            int prior = join_job();
            if (continuation && prior != 0) blocked_chain = true;
            uint8_t address = (unsigned)address_byte >> 1;
            bool read = address_byte & 1;
            bool accept = cfg->address[address];
            const rule_t *request = NULL;
            if (accept && read) {
                request = filter_match(cfg, PH_READ_REQUEST, address, NULL, 0, false);
                accept = !blocked_chain && !(request && request->action == ACT_BLOCK);
                if (accept) {
                    uint8_t dest = request && request->destination != 255 ? request->destination : address;
                    accept = execute(CMD_READ_START, cfg, dest, 0) == 0;
                }
            }
            if (!up_ack(cfg, accept)) { boundary = RX_TIMEOUT; break; }
            if (!accept) { boundary = up_boundary(cfg); continuation = true; continue; }
            if (!read) {
                size_t count = 0;
                bool overflow = false;
                for (;;) {
                    int value = up_receive(cfg);
                    if (value < 0) { boundary = value; break; }
                    if (count < cfg->max_write) write_data[count++] = value;
                    else overflow = true;
                    if (!up_ack(cfg, !overflow)) { boundary = RX_TIMEOUT; break; }
                }
                if (!overflow && boundary != RX_TIMEOUT) {
                    submit(CMD_WRITE, cfg, address, 0, write_data, count, boundary == RX_START);
                } else {
                    blocked_chain = true;
                    submit(CMD_STOP, cfg, 0, 0, NULL, 0, false);
                }
            } else {
                size_t offset = 0;
                bool blocked = false, failed = false;
                for (;;) {
                    int value = failed ? -1 : execute(CMD_READ_BYTE, cfg, 0, 0);
                    if (value < 0) failed = true;
                    uint8_t output = cfg->fill;
                    if (!failed) {
                        if (offset < GATE_BYTES) read_prefix[offset] = value;
                        const rule_t *r = blocked ? NULL : filter_match(cfg, PH_READ_RESPONSE, address,
                            read_prefix, offset < GATE_BYTES ? offset + 1 : GATE_BYTES, true);
                        if (r && r->action == ACT_BLOCK) blocked = true;
                        if (!blocked) output = filter_byte(r, offset, (uint8_t)value);
                    }
                    int nack = up_send(cfg, output);
                    if (!failed && execute(CMD_READ_ACK, cfg, 0, nack != 0) < 0) failed = true;
                    if (nack != 0) { boundary = nack < 0 ? RX_TIMEOUT : up_boundary(cfg); break; }
                    // Only configured offsets need indexing; avoid wrapping on arbitrarily long reads.
                    if (offset < GATE_BYTES) ++offset;
                }
            }
            continuation = true;
        }
        // On STOP, service termination asynchronously so core 1 can observe the next START.
        if (job.command == CMD_NONE) submit(CMD_STOP, cfg, 0, 0, NULL, 0, false);
        if (boundary == RX_TIMEOUT) {
            release(UP_SDA); release(UP_SCL);
            // Core 0 owns the fault counter; a downstream STOP also clears held bus state.
        }
    }
}

void bridge_init(void) {
    const unsigned pins[] = { UP_SDA, UP_SCL, DOWN_SDA, DOWN_SCL };
    for (unsigned i = 0; i < sizeof(pins) / sizeof(pins[0]); ++i) {
        gpio_init(pins[i]); gpio_put(pins[i], 0); gpio_set_dir(pins[i], GPIO_IN);
        gpio_disable_pulls(pins[i]);
    }
    // Forward by default, including before the first USB configuration arrives.
    config_default(&gate_configs[0]);
}
