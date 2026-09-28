// Experimental GPIO bridge: validate timing with a logic analyzer before use.
// Core 1 exclusively owns upstream pins; core 0 owns downstream pins.
#include "gpio_bridge.h"
#ifdef GATE_BRIDGE_TEST
#include "bridge_test_io.h"
#else
#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/structs/sio.h"
#include "hardware/sync.h"
#endif

config_t gate_configs[2];
volatile int gate_active_config = 0;
volatile int gate_pending_config = -1;
volatile uint32_t gate_faults = 0;

enum { CMD_NONE, CMD_ADDRESS, CMD_WRITE_BYTE, CMD_READ_BYTE, CMD_READ_ACK, CMD_STOP };
enum { RX_STOP = -1, RX_START = -2, RX_TIMEOUT = -3 };
static struct {
    volatile unsigned command;
    volatile int result;
    const config_t *cfg;
    uint8_t address, value;
} job;
static uint8_t write_data[GATE_BYTES], read_prefix[GATE_BYTES];
static uint32_t deadline, half_cycles;
static bool down_open;
static bool up_failed;
static address_callback_t address_callback = filter_address;
static data_callback_t data_callback = filter_data;

void bridge_set_callbacks(address_callback_t address, data_callback_t data) {
    address_callback = address ? address : filter_address;
    data_callback = data ? data : filter_data;
}

static inline bool expired(uint32_t end) { return (int32_t)(time_us_32() - end) >= 0; }
#ifndef GATE_BRIDGE_TEST
static inline bool high(unsigned pin) { return (sio_hw->gpio_in & (1u << pin)) != 0; }
static inline void low(unsigned pin) { sio_hw->gpio_oe_set = 1u << pin; }
static inline void release(unsigned pin) { sio_hw->gpio_oe_clr = 1u << pin; }
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

static int down_write(uint8_t value) {
    for (unsigned i = 0; i < 8; ++i) {
        if (!down_bit(value & 0x80)) return -1;
        value <<= 1;
    }
    return down_sample(); // 0=ACK, 1=NACK, -1=timeout
}
#endif

void bridge_service(void) {
    unsigned command = job.command;
    if (command == CMD_NONE) return;
    __dmb();
    const config_t *cfg = job.cfg;
    deadline = time_us_32() + cfg->timeout;
    half_cycles = (clock_get_hz(clk_sys) + 2 * cfg->speed - 1) / (2 * cfg->speed);
    int result = 0;
    if (command == CMD_ADDRESS) {
        result = down_start() ? down_write(job.value) : -1;
    } else if (command == CMD_WRITE_BYTE) {
        result = down_write(job.value);
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
#ifdef GATE_BRIDGE_TEST
        bridge_service();
#endif
        if (expired(end)) {
            // Release the host on timeout, but retain the mailbox until core 0 finishes.
            release(UP_SDA); release(UP_SCL); up_failed = true;
        }
    }
    __dmb();
    return up_failed ? -1 : job.result;
}

static void submit(unsigned command, const config_t *cfg, uint8_t address,
                   uint8_t value) {
    job.cfg = cfg; job.address = address; job.value = value;
    __dmb(); job.command = command;
}

static int execute(unsigned command, const config_t *cfg, uint8_t address, uint8_t value) {
    join_job(); submit(command, cfg, address, value);
    return join_job();
}

#ifndef GATE_BRIDGE_TEST
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
#endif

static void activate_pending(void) {
    int pending = gate_pending_config;
    if (pending >= 0 && job.command == CMD_NONE) {
        __dmb(); gate_active_config = pending;
        __dmb(); gate_pending_config = -1;
    }
}

#ifndef GATE_BRIDGE_TEST
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
#endif

void bridge_core1(void) {
    save_and_disable_interrupts();
    for (;;) {
        wait_start();
        up_failed = false;
        const config_t *cfg = &gate_configs[gate_active_config];
        write_context_t write = {false, 0, write_data, 0};
        int boundary = RX_START;
        while (boundary == RX_START) {
            int address_byte = up_receive(cfg);
            if (address_byte < 0) { boundary = address_byte; break; }
            join_job(); // SCL is now held low while the previous STOP finishes.
            uint8_t address = (uint8_t)((unsigned)address_byte >> 1);
            bool read = address_byte & 1;
            // up_receive holds SCL low after bit 8, throughout callback and device ACK.
            address_result_t address_decision = address_callback(cfg, address, read, &write);
            bool accept = cfg->address[address] && !address_decision.block && !up_failed;
            if (!read) write = (write_context_t){false, address, write_data, 0};
            if (accept)
                accept = execute(CMD_ADDRESS, cfg, 0, (uint8_t)((address_decision.destination << 1) | read)) == 0;
            if (!up_ack(cfg, accept)) { boundary = RX_TIMEOUT; break; }
            if (!accept) {
                execute(CMD_STOP, cfg, 0, 0);
                boundary = up_boundary(cfg);
                continue;
            }
            if (!read) {
                write.valid = true;
                for (;;) {
                    int value = up_receive(cfg);
                    if (value < 0) { boundary = value; break; }
                    bool ack = false;
                    if (write.count < cfg->max_write) {
                        write_data[write.count++] = (uint8_t)value;
                        data_result_t decision = data_callback(cfg, address, false,
                                                               write_data, write.count, NULL);
                        if (!decision.block && decision.ack != NACK_FORCE)
                            ack = execute(CMD_WRITE_BYTE, cfg, 0,
                                          decision.modify ? decision.value : (uint8_t)value) == 0;
                    }
                    if (!up_ack(cfg, ack)) { boundary = RX_TIMEOUT; break; }
                    if (!ack) {
                        // Bytes already ACKed remain committed. Do not send any more in this segment.
                        execute(CMD_STOP, cfg, 0, 0);
                        boundary = up_boundary(cfg);
                        break;
                    }
                }
            } else {
                size_t count = 0;
                bool blocked = false, ended = false;
                for (;;) {
                    int value = ended ? -1 : execute(CMD_READ_BYTE, cfg, 0, 0);
                    if (value < 0) ended = true;
                    uint8_t output = cfg->fill;
                    ack_mode_t ack = ACK_HOST;
                    if (!ended) {
                        read_prefix[count++] = (uint8_t)value;
                        data_result_t decision = data_callback(cfg, address, true,
                                                               read_prefix, count, &write);
                        blocked = blocked || decision.block; // Legacy read block: replace with fill.
                        ack = decision.ack;
                        if (!blocked) output = decision.modify ? decision.value : (uint8_t)value;
                    }
                    int host_nack = up_send(cfg, output);
                    if (!ended) {
                        bool device_nack = ack == NACK_FORCE || (ack == ACK_HOST && host_nack != 0);
                        // Bound the complete prefix: never call back with silently truncated context.
                        if (count == GATE_BYTES || host_nack < 0) device_nack = true;
                        if (execute(CMD_READ_ACK, cfg, 0, device_nack) < 0) ended = true;
                        ended = ended || device_nack;
                    }
                    if (host_nack != 0) {
                        boundary = host_nack < 0 ? RX_TIMEOUT : up_boundary(cfg);
                        break;
                    }
                    // A forced NACK ends downstream reads. Further host clocks receive fill.
                }
            }
        }
        // SCL may already be high on a boundary: never stretch by pulling it low there.
        // Complete downstream STOP asynchronously while watching for the next host START.
        if (job.command == CMD_NONE) submit(CMD_STOP, cfg, 0, 0);
        if (boundary == RX_TIMEOUT) {
            release(UP_SDA); release(UP_SCL);
        }
    }
}

void bridge_init(void) {
#ifndef GATE_BRIDGE_TEST
    const unsigned pins[] = { UP_SDA, UP_SCL, DOWN_SDA, DOWN_SCL };
    for (unsigned i = 0; i < sizeof(pins) / sizeof(pins[0]); ++i) {
        gpio_init(pins[i]); gpio_put(pins[i], 0); gpio_set_dir(pins[i], GPIO_IN);
        gpio_disable_pulls(pins[i]);
    }
#endif
    // Forward by default, including before the first USB configuration arrives.
    config_default(&gate_configs[0]);
}
