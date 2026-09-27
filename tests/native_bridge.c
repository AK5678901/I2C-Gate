#include <setjmp.h>
#include <string.h>
#define GATE_BRIDGE_TEST
#include "../firmware/gpio_bridge.c"
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

// Exercise the real bridge_core1 and mailbox service, substituting byte transport.
// Event codes: 1=start, 2=stop, 3=down byte, 4=up ack, 5=up data,
// 6=down ack bit, 7=address callback, 8=data callback, 9=down read byte,
// 10=callback WRITE context length, 11=context address, 12=context last byte.
enum { CAPACITY = 20000 };
static int rx[CAPACITY], host_acks[CAPACITY], device_acks[CAPACITY];
static uint8_t reads[CAPACITY];
static size_t rx_count, rx_pos, host_count, host_pos, device_count, device_pos;
static size_t read_count, read_pos;
static unsigned read_bit;
static int events[CAPACITY][2], event_count, violations, starts;
static bool stretched;
static jmp_buf finished;
static uint32_t ticks;

static void record(int kind, int value) {
    if (event_count >= CAPACITY) longjmp(finished, 1);
    events[event_count][0] = kind; events[event_count++][1] = value;
}
static void require_stretch(void) { if (!stretched) ++violations; }
static uint32_t time_us_32(void) { return ticks++; }
static uint32_t clock_get_hz(unsigned clock) { (void)clock; return 125000000; }
static void release(unsigned pin) { if (pin == UP_SCL) stretched = false; }
static bool down_start(void) { require_stretch(); record(1, 0); down_open = true; return true; }
static bool down_stop(void) { record(2, 0); down_open = false; return true; }
static int down_write(uint8_t value) {
    require_stretch(); record(3, value);
    return device_pos < device_count ? device_acks[device_pos++] : 0;
}
static int down_sample(void) {
    require_stretch();
    if (read_pos >= read_count) return -1;
    if (!read_bit) record(9, reads[read_pos]);
    int value = (reads[read_pos] >> (7 - read_bit)) & 1;
    if (++read_bit == 8) { read_bit = 0; ++read_pos; }
    return value;
}
static bool down_bit(bool value) { require_stretch(); record(6, value); return true; }
static int up_receive(const config_t *cfg) {
    (void)cfg;
    if (rx_pos >= rx_count) longjmp(finished, 1);
    int value = rx[rx_pos++];
    stretched = value >= 0;
    return value;
}
static bool up_ack(const config_t *cfg, bool ack) {
    (void)cfg; require_stretch(); record(4, ack);
    return !up_failed;
}
static int up_send(const config_t *cfg, uint8_t value) {
    (void)cfg; require_stretch(); record(5, value);
    return host_pos < host_count ? host_acks[host_pos++] : 1;
}
static int up_boundary(const config_t *cfg) {
    int value;
    do {
        value = up_receive(cfg);
        if (value >= 0 && !up_ack(cfg, false)) return RX_TIMEOUT;
    } while (value >= 0);
    return value;
}
static void wait_start(void) {
    bridge_service();
    activate_pending();
    stretched = false;
    // A remaining scripted segment after STOP is a fresh transaction.
    if (starts++ && rx_pos >= rx_count) longjmp(finished, 1);
}
static void context_events(const write_context_t *write) {
    record(10, write && write->valid ? (int)write->count : -1);
    if (write && write->valid) {
        record(11, write->address);
        record(12, write->count ? write->data[write->count - 1] : -1);
    }
}
static address_result_t test_address(const config_t *cfg, uint8_t address, bool read,
                                      const write_context_t *write) {
    require_stretch(); record(7, (address << 1) | read); context_events(write);
    return filter_address(cfg, address, read, write);
}
static data_result_t test_data(const config_t *cfg, uint8_t address, bool read,
                               const uint8_t *data, size_t count, const write_context_t *write) {
    require_stretch(); record(8, (int)count); context_events(write);
    return filter_data(cfg, address, read, data, count, write);
}
EXPORT int bridge_test_config(const uint8_t *data, size_t size) {
    return config_decode(&gate_configs[0], data, size);
}
EXPORT void bridge_test_input(const int *input, size_t count, const uint8_t *data, size_t size) {
    rx_count = count < CAPACITY ? count : CAPACITY;
    read_count = size < CAPACITY ? size : CAPACITY;
    memcpy(rx, input, rx_count * sizeof(int)); memcpy(reads, data, read_count);
}
EXPORT void bridge_test_acks(const int *host, size_t hc, const int *device, size_t dc) {
    host_count = hc < CAPACITY ? hc : CAPACITY; device_count = dc < CAPACITY ? dc : CAPACITY;
    memcpy(host_acks, host, host_count * sizeof(int));
    memcpy(device_acks, device, device_count * sizeof(int));
}
EXPORT int bridge_test_run(void) {
    rx_pos = host_pos = device_pos = read_pos = 0; read_bit = 0;
    event_count = violations = starts = 0; ticks = 0;
    down_open = up_failed = stretched = false;
    gate_active_config = 0; gate_pending_config = -1; gate_faults = 0;
    memset(&job, 0, sizeof(job));
    bridge_set_callbacks(test_address, test_data);
    if (!setjmp(finished)) bridge_core1();
    return violations;
}
EXPORT int bridge_test_event_count(void) { return event_count; }
EXPORT int bridge_test_event(int index, int field) { return events[index][field]; }
