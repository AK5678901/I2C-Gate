// Byte-level transport seam. Production builds always use the GPIO implementation.
#ifndef BRIDGE_TEST_IO_H
#define BRIDGE_TEST_IO_H
#define clk_sys 0
static uint32_t time_us_32(void);
static uint32_t clock_get_hz(unsigned clock);
static void __dmb(void) {}
static void save_and_disable_interrupts(void) {}
static void release(unsigned pin);
static bool down_start(void);
static bool down_stop(void);
static int down_write(uint8_t value);
static int down_sample(void);
static bool down_bit(bool value);
static int up_receive(const config_t *cfg);
static bool up_ack(const config_t *cfg, bool ack);
static int up_send(const config_t *cfg, uint8_t value);
static int up_boundary(const config_t *cfg);
static void wait_start(void);
#endif
